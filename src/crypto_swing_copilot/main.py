"""
Daily pipeline orchestrator — The Tenth Floor AI.

Fetches OHLCV + sentiment, runs the 4-agent pipeline, logs approved
signals to SQLite, and posts a consolidated embed to Discord.

Usage::

    python -m crypto_swing_copilot.main                    # full universe
    python -m crypto_swing_copilot.main BTCUSDT ETHUSDT    # specific pairs
    python -m crypto_swing_copilot.main --dry-run           # skip Discord + DB
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from crypto_swing_copilot.agents.base import load_risk_profile
from crypto_swing_copilot.agents.quant_agent import QuantAgent
from crypto_swing_copilot.agents.risk_agent import RiskAgent
from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent
from crypto_swing_copilot.agents.strategy_agent import StrategyAgent
from crypto_swing_copilot.data.market_data import MarketDataFetcher
from crypto_swing_copilot.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupProposal,
    TrendRegime,
)
from crypto_swing_copilot.data.sentiment import SentimentFetcher
from crypto_swing_copilot.db.signal_logger import SignalLogger
from crypto_swing_copilot.features.pair_snapshot import SnapshotBuilder
from crypto_swing_copilot.notifications.discord_notifier import DiscordNotifier

try:
    from langfuse.decorators import langfuse_context, observe as lf_observe
except ImportError:  # pragma: no cover
    langfuse_context = None  # type: ignore[assignment]

    def lf_observe(**_kw):  # type: ignore[misc]
        def _noop(fn):  # type: ignore[no-untyped-def]
            return fn
        return _noop

logger = logging.getLogger(__name__)

# Timeframe preference for tie-breaking (higher index = preferred)
_TF_PREFERENCE = {"4h": 0, "1d": 1}


def _dedup_per_pair(approved: list[PlaybookEntry]) -> list[PlaybookEntry]:
    """Keep one signal per pair — highest R:R wins, prefer 1d on tie."""
    best: dict[str, PlaybookEntry] = {}
    for entry in approved:
        existing = best.get(entry.symbol)
        if existing is None:
            best[entry.symbol] = entry
            continue
        # Higher R:R wins
        if entry.reward_risk_ratio > existing.reward_risk_ratio:
            best[entry.symbol] = entry
        elif entry.reward_risk_ratio == existing.reward_risk_ratio:
            # Tie: prefer longer timeframe (1d > 4h)
            if _TF_PREFERENCE.get(entry.timeframe, 0) > _TF_PREFERENCE.get(existing.timeframe, 0):
                best[entry.symbol] = entry

    deduped = list(best.values())
    dropped = len(approved) - len(deduped)
    if dropped:
        logger.info("Dedup: kept %d signals, dropped %d duplicate pair(s)", len(deduped), dropped)
    return deduped


@lf_observe(name="daily_pipeline")
def run_pipeline(
    pairs: list[str] | None = None,
    *,
    dry_run: bool = False,
) -> list[PlaybookEntry]:
    """Execute the full daily pipeline.

    Parameters
    ----------
    pairs:
        Override the universe.  ``None`` uses ``config/universe.json``.
    dry_run:
        If ``True``, skip SQLite logging and Discord posting.

    Returns
    -------
    list[PlaybookEntry]
        All entries produced by RiskAgent (approved + rejected).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("=== THE TENTH FLOOR — daily run %s ===", today)

    # ── 1. Fetch market data ────────────────────────────────────────
    logger.info("Step 1/6: Fetching OHLCV data")
    fetcher = MarketDataFetcher()
    ohlcv_data = fetcher.fetch_universe(pairs=pairs)

    # ── 2. Fetch sentiment (once, shared across all pairs) ──────────
    logger.info("Step 2/6: Fetching sentiment snapshot")
    sentiment_fetcher = SentimentFetcher()
    sentiment_snapshot = sentiment_fetcher.fetch_snapshot()

    # ── 3. Build PairSnapshots ──────────────────────────────────────
    logger.info("Step 3/6: Building pair snapshots")
    builder = SnapshotBuilder()
    snapshots = builder.build_universe(ohlcv_data, sentiment=sentiment_snapshot)

    if not snapshots:
        logger.warning("No snapshots built — nothing to analyse")
        return []

    logger.info("Built %d snapshots", len(snapshots))

    # ── 4. Run agent pipeline per snapshot ──────────────────────────
    logger.info("Step 4/6: Running agent pipeline (%d snapshots)", len(snapshots))
    quant_agent = QuantAgent()
    sentiment_agent = SentimentAgent()
    strategy_agent = StrategyAgent()

    # SentimentAgent runs once on the shared snapshot
    try:
        sentiment_signal = sentiment_agent.run(sentiment_snapshot)
        logger.info(
            "SentimentAgent done  bias=%s  fg=%d",
            sentiment_signal.bias.value,
            sentiment_signal.fear_greed_value,
        )
    except Exception:
        logger.exception("SentimentAgent failed — aborting pipeline")
        return []

    # Trend-regime gate: reject setups where QuantAgent says strong_downtrend.
    # Buying a death cross just because F&G is low is catching a falling knife.
    _REJECT_REGIMES = {TrendRegime.STRONG_DOWNTREND}

    # Volume confirmation: contrarian LONGs in a downtrend require above-average
    # volume to confirm buyers are actually stepping in (not a slow bleed).
    _VOLUME_CONFIRM_REGIMES = {TrendRegime.DOWNTREND}
    _MIN_VOLUME_RATIO = 1.3  # latest bar volume must be >= 1.3× the 20-bar SMA

    proposals: list[tuple[SetupProposal, float]] = []
    # Track BTC's quant result for the correlation guard
    btc_approved = False

    # ── 4a. Collect 1d trend scores for multi-timeframe gate ──────────
    # A 4h BUY in a 1d downtrend is a lower-timeframe bounce trap.
    # Require 1d trend_score >= 0.3 (at least "sideways") for 4h BUY.
    _MTF_MIN_DAILY_SCORE = 0.3
    daily_trend_scores: dict[str, float] = {}
    for snap in snapshots:
        if snap.timeframe == "1d" and snap.indicators.trend_score is not None:
            daily_trend_scores[snap.symbol] = snap.indicators.trend_score

    for snap in snapshots:
        try:
            quant_signal = quant_agent.run(snap)
            logger.info(
                "QuantAgent  %s %s  trend=%s  llm_conf=%.2f  trend_score=%s",
                snap.symbol, snap.timeframe,
                quant_signal.trend_regime.value, quant_signal.confidence,
                snap.indicators.trend_score,
            )

            # Gate: skip strong downtrends — no buying falling knives
            if quant_signal.trend_regime in _REJECT_REGIMES:
                logger.info(
                    "Trend gate SKIP  %s %s  regime=%s — falling knife",
                    snap.symbol, snap.timeframe,
                    quant_signal.trend_regime.value,
                )
                continue

            proposal = strategy_agent.run(snap, quant_signal, sentiment_signal)
            logger.info(
                "StrategyAgent  %s %s  action=%s  direction=%s  RR=%.1f",
                snap.symbol, snap.timeframe,
                proposal.action.value, proposal.direction.value,
                proposal.reward_risk_ratio,
            )

            # Multi-timeframe gate: 4h BUY requires 1d trend to be at least sideways.
            # A 4h bounce in a 1d downtrend is a trap — skip it.
            if (
                proposal.action.value == "buy"
                and snap.timeframe != "1d"
                and snap.symbol in daily_trend_scores
                and daily_trend_scores[snap.symbol] < _MTF_MIN_DAILY_SCORE
            ):
                logger.info(
                    "MTF gate SKIP  %s %s  daily_score=%.2f < %.1f — "
                    "4h bounce in daily downtrend",
                    snap.symbol, snap.timeframe,
                    daily_trend_scores[snap.symbol], _MTF_MIN_DAILY_SCORE,
                )
                continue

            # Volume confirmation gate: in downtrends, require above-average
            # volume on BUY proposals.  Low-volume "bounces" in downtrends
            # are traps — smart money stepping in shows up as volume surges.
            if (
                proposal.action.value == "buy"
                and quant_signal.trend_regime in _VOLUME_CONFIRM_REGIMES
            ):
                vol_ratio = snap.indicators.volume_ratio
                if vol_ratio is not None and vol_ratio < _MIN_VOLUME_RATIO:
                    logger.info(
                        "Volume gate SKIP  %s %s  vol_ratio=%.2f < %.1f — "
                        "no buyer confirmation in downtrend",
                        snap.symbol, snap.timeframe, vol_ratio, _MIN_VOLUME_RATIO,
                    )
                    continue

            # Use deterministic trend_score for gating/conviction;
            # fall back to LLM confidence only when trend_score unavailable.
            score = snap.indicators.trend_score
            if score is None:
                score = quant_signal.confidence
            proposals.append((proposal, score))

            # Track if BTC got a buy proposal
            if snap.symbol == "BTCUSDT" and proposal.action.value == "buy":
                btc_approved = True

        except Exception:
            logger.exception("Agent pipeline failed for %s %s — skipping", snap.symbol, snap.timeframe)

    if not proposals:
        logger.warning("No proposals generated — all pairs failed or were empty")

    # ── 5. RiskAgent — filter + conviction tiers ────────────────────
    logger.info("Step 5/6: Running RiskAgent on %d proposals", len(proposals))
    risk_agent = RiskAgent()
    entries = risk_agent.run(proposals) if proposals else []

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    logger.info(
        "RiskAgent done  total=%d  approved=%d  rejected=%d",
        len(entries),
        len(approved),
        len(entries) - len(approved),
    )

    # ── 5b. Dedup: one signal per pair per day ───────────────────────
    # When both timeframes approve the same pair, keep the higher R:R.
    # On tie, prefer 1d (better alignment with swing trading horizon).
    approved = _dedup_per_pair(approved)

    # ── 5c. BTC-correlation guard ─────────────────────────────────────
    # If BTC itself was rejected/skipped, cap alt signals to max 2.
    # Crypto is BTC-correlated; if the leader looks bad, limit exposure.
    if not btc_approved and len(approved) > 2:
        alt_only = [e for e in approved if e.symbol != "BTCUSDT"]
        btc_signals = [e for e in approved if e.symbol == "BTCUSDT"]
        # Sort alts by confidence descending, keep top 2
        alt_only.sort(key=lambda e: e.confidence_score, reverse=True)
        approved = btc_signals + alt_only[:2]
        logger.info(
            "BTC-correlation guard: BTC not approved — capped alts to %d",
            len(approved),
        )

    # ── 5d. Signal cap — publish top N by confidence ──────────────────
    risk_profile = load_risk_profile()
    max_signals = risk_profile.get("max_daily_signals", 5)
    if len(approved) > max_signals:
        approved.sort(key=lambda e: e.confidence_score, reverse=True)
        dropped = len(approved) - max_signals
        approved = approved[:max_signals]
        logger.info("Signal cap: kept top %d, dropped %d", max_signals, dropped)

    # Re-rank after all filtering (1 = highest confidence)
    for i, entry in enumerate(approved, 1):
        approved[i - 1] = entry.model_copy(update={"rank": i})

    # ── 6. Persist + notify ─────────────────────────────────────────
    logger.info("Step 6/6: Logging signals and posting to Discord")

    if dry_run:
        logger.info("DRY RUN — skipping SQLite + Discord")
        for e in approved:
            logger.info(
                "  [DRY] %s %s  %s  conviction=%s  RR=%.1f  risk=%.0f%%",
                e.symbol, e.timeframe, e.direction.value.upper(),
                e.conviction.upper(), e.reward_risk_ratio,
                e.suggested_risk_pct * 100,
            )
        return entries

    # Log to SQLite
    trace_id = None
    if langfuse_context is not None:
        try:
            trace_id = langfuse_context.get_current_trace_id()
        except Exception:
            pass

    with SignalLogger() as signal_logger:
        for entry in approved:
            signal_id = signal_logger.log(entry, langfuse_trace_id=trace_id)
            if signal_id:
                logger.info("Logged signal %s", signal_id)

        open_count = signal_logger.open_signal_count()

    # Post to Discord
    notifier = DiscordNotifier()
    posted = notifier.post(approved, open_count=open_count, report_date=today)
    if posted:
        logger.info("Discord embed posted  signals=%d  open=%d", len(approved), open_count)
    else:
        logger.warning("Discord post failed or webhook not configured")

    logger.info("=== Pipeline complete ===")
    return entries


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="The Tenth Floor AI — daily signal pipeline",
    )
    parser.add_argument(
        "pairs",
        nargs="*",
        default=None,
        help="Specific pairs to analyse (default: full universe from config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline but skip SQLite logging and Discord posting",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args(argv)
    # Convert empty list to None so fetch_universe uses config
    if not args.pairs:
        args.pairs = None
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    entries = run_pipeline(pairs=args.pairs, dry_run=args.dry_run)

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    print(f"\nDone. {len(approved)} approved signals from {len(entries)} total entries.")


if __name__ == "__main__":
    main()
