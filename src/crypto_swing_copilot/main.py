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

from crypto_swing_copilot.agents.quant_agent import QuantAgent
from crypto_swing_copilot.agents.risk_agent import RiskAgent
from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent
from crypto_swing_copilot.agents.strategy_agent import StrategyAgent
from crypto_swing_copilot.data.market_data import MarketDataFetcher
from crypto_swing_copilot.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupProposal,
)
from crypto_swing_copilot.data.sentiment import SentimentFetcher
from crypto_swing_copilot.db.signal_logger import SignalLogger
from crypto_swing_copilot.features.pair_snapshot import SnapshotBuilder
from crypto_swing_copilot.notifications.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


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
    sentiment_signal = sentiment_agent.run(sentiment_snapshot)
    logger.info(
        "SentimentAgent done  bias=%s  fg=%d",
        sentiment_signal.bias.value,
        sentiment_signal.fear_greed_value,
    )

    proposals: list[tuple[SetupProposal, float]] = []

    for snap in snapshots:
        try:
            quant_signal = quant_agent.run(snap)
            logger.info(
                "QuantAgent  %s %s  trend=%s  confidence=%.2f",
                snap.symbol, snap.timeframe,
                quant_signal.trend_regime.value, quant_signal.confidence,
            )

            proposal = strategy_agent.run(snap, quant_signal, sentiment_signal)
            logger.info(
                "StrategyAgent  %s %s  action=%s  direction=%s  RR=%.1f",
                snap.symbol, snap.timeframe,
                proposal.action.value, proposal.direction.value,
                proposal.reward_risk_ratio,
            )

            proposals.append((proposal, quant_signal.confidence))

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
    signal_logger = SignalLogger()
    for entry in approved:
        signal_id = signal_logger.log(entry)
        if signal_id:
            logger.info("Logged signal %s", signal_id)

    open_count = signal_logger.open_signal_count()
    signal_logger.close()

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
