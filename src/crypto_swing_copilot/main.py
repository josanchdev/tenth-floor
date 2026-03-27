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
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_swing_copilot.agents.base import load_risk_profile, set_active_profile
from crypto_swing_copilot.agents.quant_agent import QuantAgent
from crypto_swing_copilot.agents.risk_agent import RiskAgent
from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent
from crypto_swing_copilot.agents.strategy_agent import StrategyAgent
from crypto_swing_copilot.config import CONFIG_DIR
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
    from langfuse.decorators import langfuse_context
    from langfuse.decorators import observe as lf_observe
except ImportError:  # pragma: no cover
    langfuse_context = None  # type: ignore[assignment]

    def lf_observe(**_kw):  # type: ignore[misc]
        def _noop(fn):  # type: ignore[no-untyped-def]
            return fn
        return _noop

logger = logging.getLogger(__name__)


@dataclass
class FunnelTracker:
    """Accumulates gate-kill counts through the pipeline."""

    pairs_analyzed: int = 0
    killed_trend_gate: int = 0
    killed_strategy_skip: int = 0
    killed_volume_gate: int = 0
    killed_rs_gate: int = 0
    killed_confidence_gate: int = 0
    killed_rr_gate: int = 0
    killed_btc_corr_gate: int = 0
    killed_sector_cap: int = 0
    killed_signal_cap: int = 0
    proposals_generated: int = 0
    approved: int = 0
    published: int = 0

    def summary_lines(self) -> list[str]:
        """Return the funnel as human-readable lines."""
        lines = [
            f"  {self.pairs_analyzed} pairs analysed",
        ]
        gates = [
            (self.killed_trend_gate, "trend regime gate"),
            (self.killed_strategy_skip, "strategy (SKIP)"),
            (self.killed_volume_gate, "volume gate"),
            (self.killed_rs_gate, "BTC relative strength"),
            (self.killed_confidence_gate, "confidence gate"),
            (self.killed_rr_gate, "R:R gate"),
            (self.killed_btc_corr_gate, "BTC correlation guard"),
            (self.killed_sector_cap, "sector diversity cap"),
            (self.killed_signal_cap, "signal cap"),
        ]
        for count, label in gates:
            if count > 0:
                lines.append(f"  {count:3d} killed at {label}")
        lines.append(f"  {self.approved:3d} approved")
        lines.append(f"  {self.published:3d} published")
        return lines


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
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    logger.info("=== THE TENTH FLOOR — daily run %s ===", today)
    funnel = FunnelTracker()

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

    funnel.pairs_analyzed = len(snapshots)
    logger.info("Built %d snapshots", len(snapshots))

    # ── 4. Run agent pipeline per snapshot ──────────────────────────
    logger.info("Step 4/6: Running agent pipeline (%d snapshots)", len(snapshots))
    sentiment_agent = SentimentAgent()

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

    # ── 4a. Capitulation detection ──────────────────────────────────
    # Check if Fear & Greed is in extreme fear AND rising from its trough.
    # This signals potential capitulation — exactly when contrarian trades
    # have the most edge.  Direction matters more than level.
    fg_trend = sentiment_snapshot.fear_greed_trend  # [most_recent, ..., oldest]
    fg_rising_from_extreme = False
    if len(fg_trend) >= 3 and fg_trend[0] < 25:
        trough = min(fg_trend)
        if fg_trend[0] > trough and (fg_trend[0] - trough) >= 3:
            fg_rising_from_extreme = True
            logger.info(
                "F&G rising from extreme fear: current=%d trough=%d — "
                "capitulation bypass ARMED",
                fg_trend[0], trough,
            )

    # ── 4b. Deterministic pre-filter ────────────────────────────────
    # Skip LLM calls for pairs guaranteed to be rejected.  trend_score is
    # deterministic (7-signal indicator agreement) and already computed.
    # Any pair below min_setup_confidence will be rejected by RiskAgent,
    # so burning GPU time on QuantAgent + StrategyAgent is waste.
    risk_profile = load_risk_profile()
    min_confidence = risk_profile.get("min_setup_confidence", 0.65)
    _STRONG_DOWNTREND_SCORE = 0.15  # ≈ 0–1/7 bullish signals

    llm_candidates = []
    for snap in snapshots:
        score = snap.indicators.trend_score
        if score is not None and score < min_confidence:
            if score <= _STRONG_DOWNTREND_SCORE:
                # Would fail Gate 1 (trend regime) — check capitulation bypass
                has_rsi_div = snap.indicators.rsi_divergence is True
                if fg_rising_from_extreme and has_rsi_div:
                    llm_candidates.append(snap)  # capitulation bypass — needs LLM
                    logger.info(
                        "Pre-filter PASS  %s  trend_score=%.2f — "
                        "capitulation bypass (F&G rising + RSI div)",
                        snap.symbol, score,
                    )
                else:
                    funnel.killed_trend_gate += 1
                    logger.info(
                        "Pre-filter SKIP  %s  trend_score=%.2f — "
                        "strong downtrend (no LLM needed)",
                        snap.symbol, score,
                    )
            else:
                # Below confidence threshold — RiskAgent would reject
                funnel.killed_strategy_skip += 1
                logger.info(
                    "Pre-filter SKIP  %s  trend_score=%.2f < %.2f — "
                    "below confidence threshold (no LLM needed)",
                    snap.symbol, score, min_confidence,
                )
        else:
            llm_candidates.append(snap)

    logger.info(
        "Pre-filter: %d/%d pairs need LLM evaluation (saved %d LLM call pairs)",
        len(llm_candidates), len(snapshots),
        len(snapshots) - len(llm_candidates),
    )

    # Trend-regime gate: reject setups where QuantAgent says strong_downtrend.
    # Still checked as safety net for pairs that pass pre-filter.
    _REJECT_REGIMES = {TrendRegime.STRONG_DOWNTREND}

    # Volume confirmation: contrarian LONGs in a downtrend require above-average
    # volume to confirm buyers are actually stepping in (not a slow bleed).
    _VOLUME_CONFIRM_REGIMES = {TrendRegime.DOWNTREND}
    _MIN_VOLUME_RATIO = 1.3  # latest bar volume must be >= 1.3× the 20-bar SMA

    proposals: list[tuple[SetupProposal, float]] = []
    # Track BTC's quant result for the correlation guard
    btc_approved = False

    # ── 4c. BTC relative strength baseline ────────────────────────────
    # Alts underperforming BTC in a fear environment are weak — skip them.
    btc_pct_change: float | None = None
    for snap in snapshots:
        if snap.symbol == "BTCUSDT" and len(snap.recent_closes) >= 2:
            newest, oldest = snap.recent_closes[0], snap.recent_closes[-1]
            if oldest > 0:
                btc_pct_change = (newest - oldest) / oldest
            break

    # ── 4d. LLM pipeline — only for qualifying pairs ────────────────
    quant_agent = QuantAgent()
    strategy_agent = StrategyAgent()

    for snap in llm_candidates:
        try:
            quant_signal = quant_agent.run(snap)
            logger.info(
                "QuantAgent  %s %s  trend=%s  llm_conf=%.2f  trend_score=%s",
                snap.symbol, snap.timeframe,
                quant_signal.trend_regime.value, quant_signal.confidence,
                snap.indicators.trend_score,
            )

            # Gate: skip strong downtrends — safety net for LLM disagreement
            if quant_signal.trend_regime in _REJECT_REGIMES:
                has_rsi_div = snap.indicators.rsi_divergence is True
                if fg_rising_from_extreme and has_rsi_div:
                    logger.info(
                        "Trend gate BYPASS  %s %s  regime=%s — "
                        "capitulation setup (F&G rising + RSI divergence)",
                        snap.symbol, snap.timeframe,
                        quant_signal.trend_regime.value,
                    )
                else:
                    logger.info(
                        "Trend gate SKIP  %s %s  regime=%s — falling knife",
                        snap.symbol, snap.timeframe,
                        quant_signal.trend_regime.value,
                    )
                    funnel.killed_trend_gate += 1
                    continue

            proposal = strategy_agent.run(snap, quant_signal, sentiment_signal)
            logger.info(
                "StrategyAgent  %s %s  action=%s  direction=%s  RR=%.1f",
                snap.symbol, snap.timeframe,
                proposal.action.value, proposal.direction.value,
                proposal.reward_risk_ratio,
            )

            # Strategy gate: SKIP/HOLD actions don't proceed
            if proposal.action.value != "buy":
                funnel.killed_strategy_skip += 1
                continue

            # Volume confirmation gate: in downtrends, require above-average
            # volume on BUY proposals.
            if quant_signal.trend_regime in _VOLUME_CONFIRM_REGIMES:
                vol_ratio = snap.indicators.volume_ratio
                if vol_ratio is not None and vol_ratio < _MIN_VOLUME_RATIO:
                    logger.info(
                        "Volume gate SKIP  %s %s  vol_ratio=%.2f < %.1f — "
                        "no buyer confirmation in downtrend",
                        snap.symbol, snap.timeframe, vol_ratio, _MIN_VOLUME_RATIO,
                    )
                    funnel.killed_volume_gate += 1
                    continue

            # Relative strength gate: in fear environments, skip alts that
            # are underperforming BTC.
            if (
                snap.symbol != "BTCUSDT"
                and btc_pct_change is not None
                and sentiment_signal.bias.value in ("fear", "extreme_fear")
                and len(snap.recent_closes) >= 2
            ):
                alt_oldest = snap.recent_closes[-1]
                if alt_oldest > 0:
                    alt_pct = (snap.recent_closes[0] - alt_oldest) / alt_oldest
                    if alt_pct < btc_pct_change:
                        logger.info(
                            "RS gate SKIP  %s  alt=%.1f%% < btc=%.1f%% — "
                            "underperforming BTC in fear market",
                            snap.symbol, alt_pct * 100, btc_pct_change * 100,
                        )
                        funnel.killed_rs_gate += 1
                        continue

            # Use deterministic trend_score for gating/conviction;
            # fall back to LLM confidence only when trend_score unavailable.
            score = snap.indicators.trend_score
            if score is None:
                score = quant_signal.confidence
            proposals.append((proposal, score))

            # Track if BTC got a buy proposal
            if snap.symbol == "BTCUSDT":
                btc_approved = True

        except Exception:
            logger.exception("Agent pipeline failed for %s %s — skipping", snap.symbol, snap.timeframe)

    funnel.proposals_generated = len(proposals)

    if not proposals:
        logger.warning("No proposals generated — all pairs failed or were empty")

    # ── 5. RiskAgent — filter + conviction tiers ────────────────────
    logger.info("Step 5/6: Running RiskAgent on %d proposals", len(proposals))
    risk_agent = RiskAgent()
    entries = risk_agent.run(proposals) if proposals else []

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    rejected = [e for e in entries if e.verdict != PlaybookVerdict.APPROVED]
    logger.info(
        "RiskAgent done  total=%d  approved=%d  rejected=%d",
        len(entries),
        len(approved),
        len(rejected),
    )

    # Count RiskAgent rejection reasons for funnel
    for e in rejected:
        reason = e.verdict_reasoning.lower()
        if "r:r" in reason or "below minimum" in reason and "confidence" not in reason:
            funnel.killed_rr_gate += 1
        elif "confidence" in reason:
            funnel.killed_confidence_gate += 1
        # SHORT/SKIP/HOLD rejections are counted under strategy_skip

    # ── 5b. BTC-correlation guard ─────────────────────────────────────
    # If BTC itself was rejected/skipped, cap alt signals to max 2.
    # Crypto is BTC-correlated; if the leader looks bad, limit exposure.
    pre_corr_count = len(approved)
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
    funnel.killed_btc_corr_gate = pre_corr_count - len(approved)

    # ── 5c. Sector diversity cap ────────────────────────────────────────
    # With 26 pairs, a bullish day could produce 5 L1 signals that are
    # effectively the same trade.  Keep the best per sector.
    universe_path = CONFIG_DIR / "universe.json"
    with open(universe_path, encoding="utf-8") as fh:
        universe_cfg = json.load(fh)
    sector_map: dict[str, str] = universe_cfg.get("sectors", {})
    max_per_sector: int = universe_cfg.get("max_per_sector", 1)

    if sector_map and len(approved) > 1:
        pre_sector_count = len(approved)
        # Sort by confidence descending, then pick first N per sector
        approved.sort(key=lambda e: e.confidence_score, reverse=True)
        seen_sectors: dict[str, int] = {}
        sector_filtered: list[PlaybookEntry] = []
        for entry in approved:
            sector = sector_map.get(entry.symbol, entry.symbol)
            count = seen_sectors.get(sector, 0)
            if count < max_per_sector:
                sector_filtered.append(entry)
                seen_sectors[sector] = count + 1
            else:
                logger.info(
                    "Sector cap SKIP  %s  sector=%s — already have %d from this sector",
                    entry.symbol, sector, max_per_sector,
                )
        approved = sector_filtered
        funnel.killed_sector_cap = pre_sector_count - len(approved)

    # ── 5d. Signal cap — publish top N by confidence ──────────────────
    max_signals = risk_profile.get("max_daily_signals", 5)
    pre_cap_count = len(approved)
    if len(approved) > max_signals:
        approved.sort(key=lambda e: e.confidence_score, reverse=True)
        approved = approved[:max_signals]
        logger.info("Signal cap: kept top %d, dropped %d", max_signals, pre_cap_count - len(approved))
    funnel.killed_signal_cap = pre_cap_count - len(approved)

    funnel.approved = len(approved)

    # Re-rank after all filtering (1 = highest confidence)
    for i, entry in enumerate(approved, 1):
        approved[i - 1] = entry.model_copy(update={"rank": i})

    # ── 6. Persist + notify ─────────────────────────────────────────
    logger.info("Step 6/6: Logging signals and posting to Discord")

    # Log funnel summary
    funnel_header = f"Pipeline Funnel — {today}"
    logger.info("%s\n%s", funnel_header, "\n".join(funnel.summary_lines()))

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

        funnel.published = funnel.approved  # all approved signals get logged

        # Log funnel to pipeline_runs table
        signal_logger.log_pipeline_run(today, funnel)

        open_count = signal_logger.open_signal_count()

    # Post to Discord
    notifier = DiscordNotifier()
    posted = notifier.post(approved, open_count=open_count, report_date=today)
    if posted:
        logger.info("Discord embed posted  signals=%d  open=%d", len(approved), open_count)
    else:
        logger.warning("Discord post failed or webhook not configured")

    # Post funnel summary to Discord (every run, including zero-signal days)
    notifier.post_funnel(today, funnel)

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
    parser.add_argument(
        "--profile",
        default=None,
        choices=["validation", "production"],
        help="Risk profile overlay (default: base config, no overlay)",
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

    if args.profile:
        set_active_profile(args.profile)
        logger.info("Active profile: %s", args.profile)

    try:
        entries = run_pipeline(pairs=args.pairs, dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("Pipeline crashed")
        if not args.dry_run:
            try:
                notifier = DiscordNotifier()
                notifier.post_error(exc)
            except Exception:
                logger.exception("Failed to post error alert to Discord")
        sys.exit(1)

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    print(f"\nDone. {len(approved)} approved signals from {len(entries)} total entries.")


if __name__ == "__main__":
    main()
