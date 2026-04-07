"""
Daily pipeline orchestrator — The Tenth Floor AI (Phase 1.5: AI-first).

Pipeline flow:
  1. Fetch OHLCV data (ccxt + yfinance)
  2. Fetch sentiment + macro indicators (F&G, VIX, DXY)
  3. Build PairSnapshots (TA indicators + structure)
  4. MacroAnalyst (1 LLM call — macro regime + per-class impact)
  5. Pre-screen (data-quality only — very permissive)
  6. TradeAnalyst (1 LLM call per candidate — BUY/SKIP + price levels)
  7. Python validation (sanity checks on LLM output)
  8. RiskReviewer (1 LLM call — portfolio-level review of all proposals)
  9. Signal cap + persist + notify

Usage::

    python -m tenth_floor.main                    # full universe
    python -m tenth_floor.main BTCUSDT AAPL       # specific symbols
    python -m tenth_floor.main --dry-run           # skip Discord + DB
    python -m tenth_floor.main --asset-class crypto # crypto only
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from tenth_floor.agents.base import load_risk_profile, set_active_profile
from tenth_floor.agents.macro_analyst import MacroAnalyst
from tenth_floor.agents.risk_reviewer import RiskReviewer
from tenth_floor.agents.trade_analyst import TradeAnalyst
from tenth_floor.data.market_data import MarketDataFetcher
from tenth_floor.data.models import (
    ConvictionTier,
    PlaybookEntry,
    PlaybookVerdict,
    ReviewVerdict,
    TradeProposal,
)
from tenth_floor.data.sentiment import SentimentFetcher
from tenth_floor.data.yfinance_data import YFinanceDataFetcher
from tenth_floor.db.signal_logger import SignalLogger
from tenth_floor.features.pair_snapshot import SnapshotBuilder
from tenth_floor.notifications.discord_notifier import DiscordNotifier
from tenth_floor.universe import Universe, load_universe
from tenth_floor.validation import validate_proposal

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
    """Accumulates pipeline stage counts for diagnostics."""

    assets_in_universe: int = 0
    assets_fetched: int = 0
    snapshots_built: int = 0
    pre_screen_passed: int = 0
    pre_screen_killed: int = 0
    trade_analyst_buy: int = 0
    trade_analyst_skip: int = 0
    trade_analyst_error: int = 0
    validation_passed: int = 0
    validation_failed: int = 0
    reviewer_approved: int = 0
    reviewer_rejected: int = 0
    signal_cap_killed: int = 0
    published: int = 0

    def summary_lines(self) -> list[str]:
        """Return the funnel as human-readable lines."""
        lines = [
            f"  {self.assets_in_universe} assets in universe",
            f"  {self.assets_fetched:3d} fetched from data sources",
            f"  {self.snapshots_built} snapshots built",
        ]
        if self.pre_screen_killed > 0:
            lines.append(f"  {self.pre_screen_killed:3d} killed at pre-screen (data quality)")
        lines.append(f"  {self.pre_screen_passed:3d} sent to TradeAnalyst")
        if self.trade_analyst_skip > 0:
            lines.append(f"  {self.trade_analyst_skip:3d} skipped by TradeAnalyst")
        if self.trade_analyst_error > 0:
            lines.append(f"  {self.trade_analyst_error:3d} TradeAnalyst errors")
        lines.append(f"  {self.trade_analyst_buy:3d} BUY proposals")
        if self.validation_failed > 0:
            lines.append(f"  {self.validation_failed:3d} failed Python validation")
        lines.append(f"  {self.validation_passed:3d} sent to RiskReviewer")
        if self.reviewer_rejected > 0:
            lines.append(f"  {self.reviewer_rejected:3d} rejected by RiskReviewer")
        if self.signal_cap_killed > 0:
            lines.append(f"  {self.signal_cap_killed:3d} killed at signal cap")
        lines.append(f"  {self.reviewer_approved:3d} approved")
        lines.append(f"  {self.published:3d} published")
        return lines


def _fetch_all_ohlcv(
    universe: Universe,
    symbols: list[str],
) -> dict[str, dict]:
    """Fetch OHLCV for all symbols, routing to the correct data source."""
    import pandas as pd

    # Group symbols by data source
    ccxt_symbols = [s for s in symbols if universe.data_source_for(s) == "ccxt"]
    yf_symbols = [s for s in symbols if universe.data_source_for(s) == "yfinance"]

    results: dict[str, dict[str, pd.DataFrame]] = {}

    if ccxt_symbols:
        logger.info("Fetching %d crypto symbols via ccxt", len(ccxt_symbols))
        ccxt_fetcher = MarketDataFetcher()
        results.update(ccxt_fetcher.fetch_universe(pairs=ccxt_symbols))

    if yf_symbols:
        logger.info("Fetching %d equity/ETF/commodity symbols via yfinance", len(yf_symbols))
        yf_fetcher = YFinanceDataFetcher()
        results.update(yf_fetcher.fetch_universe(symbols=yf_symbols))

    return results


def _fetch_macro_indicators() -> tuple[dict | None, dict | None]:
    """Fetch VIX and DXY data for MacroAnalyst.

    Returns (vix_data, dxy_data) dicts or None on failure.
    """
    try:
        import yfinance as yf

        vix_data = None
        dxy_data = None

        # VIX
        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="10d")
            if len(hist) >= 2:
                level = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                change_pct = round((level - prev) / prev * 100, 2)
                # 5-day trend
                if len(hist) >= 5:
                    start = float(hist["Close"].iloc[-5])
                    if level > start * 1.05:
                        trend = "rising"
                    elif level < start * 0.95:
                        trend = "falling"
                    else:
                        trend = "stable"
                else:
                    trend = "insufficient data"
                vix_data = {"level": round(level, 2), "change_pct": change_pct, "trend": trend}
                logger.info("VIX: %.2f (%+.2f%%) trend=%s", level, change_pct, trend)
        except Exception:
            logger.warning("Failed to fetch VIX data", exc_info=True)

        # DXY (US Dollar Index)
        try:
            dxy = yf.Ticker("DX-Y.NYB")
            hist = dxy.history(period="10d")
            if len(hist) >= 2:
                level = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                change_pct = round((level - prev) / prev * 100, 2)
                if len(hist) >= 5:
                    start = float(hist["Close"].iloc[-5])
                    if level > start * 1.01:
                        trend = "strengthening"
                    elif level < start * 0.99:
                        trend = "weakening"
                    else:
                        trend = "stable"
                else:
                    trend = "insufficient data"
                dxy_data = {"level": round(level, 2), "change_pct": change_pct, "trend": trend}
                logger.info("DXY: %.2f (%+.2f%%) trend=%s", level, change_pct, trend)
        except Exception:
            logger.warning("Failed to fetch DXY data", exc_info=True)

        return vix_data, dxy_data

    except ImportError:
        logger.warning("yfinance not installed — skipping macro indicators")
        return None, None


def _pre_screen(snapshots: list, funnel: FunnelTracker) -> list:
    """Data-quality pre-screen — extremely permissive.

    Kills only:
    - Assets with fewer than 10 recent closes
    - Assets with zero volume for 5+ consecutive days
    """
    passed = []
    for snap in snapshots:
        # Check minimum data
        if len(snap.recent_closes) < 10:
            logger.info("Pre-screen SKIP  %s — insufficient data", snap.symbol)
            funnel.pre_screen_killed += 1
            continue

        # Check for zero volume (5+ consecutive days)
        recent_vols = snap.recent_volumes[:5]
        if recent_vols and all(v == 0 for v in recent_vols):
            logger.info("Pre-screen SKIP  %s — zero volume for 5+ days", snap.symbol)
            funnel.pre_screen_killed += 1
            continue

        passed.append(snap)

    funnel.pre_screen_passed = len(passed)
    return passed


@lf_observe(name="daily_pipeline")
def run_pipeline(
    symbols: list[str] | None = None,
    *,
    dry_run: bool = False,
    asset_class: str | None = None,
) -> list[PlaybookEntry]:
    """Execute the full daily pipeline.

    Parameters
    ----------
    symbols:
        Override the universe. ``None`` uses ``config/universe.json``.
    dry_run:
        If ``True``, skip SQLite logging and Discord posting.
    asset_class:
        Filter universe to a single asset class (e.g. "crypto", "equity").

    Returns
    -------
    list[PlaybookEntry]
        All approved entries for today.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    logger.info("=== THE TENTH FLOOR — daily run %s ===", today)
    funnel = FunnelTracker()
    risk_profile = load_risk_profile()

    # ── Load universe ─────────────────────────────────────────────
    universe = load_universe()
    if symbols is None:
        symbols = universe.symbols(asset_class=asset_class)
    funnel.assets_in_universe = len(symbols)
    logger.info("Universe: %d symbols%s", len(symbols),
                f" (asset_class={asset_class})" if asset_class else "")

    # ── 1. Fetch market data ────────────────────────────────────────
    logger.info("Step 1/9: Fetching OHLCV data")
    ohlcv_data = _fetch_all_ohlcv(universe, symbols)
    funnel.assets_fetched = len(ohlcv_data)

    # ── 2. Fetch sentiment + macro indicators ────────────────────────
    logger.info("Step 2/9: Fetching sentiment + macro indicators")
    sentiment_fetcher = SentimentFetcher()
    sentiment_snapshot = sentiment_fetcher.fetch_snapshot()

    vix_data, dxy_data = _fetch_macro_indicators()

    # ── 3. Build PairSnapshots ──────────────────────────────────────
    logger.info("Step 3/9: Building pair snapshots")
    builder = SnapshotBuilder()
    snapshots = builder.build_universe(ohlcv_data, sentiment=sentiment_snapshot)

    if not snapshots:
        logger.warning("No snapshots built — nothing to analyse")
        return []

    # Enrich snapshots with asset class from universe
    enriched: list = []
    for snap in snapshots:
        ac = universe.asset_class_for(snap.symbol)
        if ac and ac != snap.asset_class:
            snap = snap.model_copy(update={"asset_class": ac})
        enriched.append(snap)
    snapshots = enriched

    funnel.snapshots_built = len(snapshots)
    logger.info("Built %d snapshots", len(snapshots))

    # ── 4. MacroAnalyst ─────────────────────────────────────────────
    logger.info("Step 4/9: Running MacroAnalyst")
    macro_analyst = MacroAnalyst()
    try:
        macro_signal = macro_analyst.run(sentiment_snapshot, vix_data, dxy_data)
        logger.info(
            "MacroAnalyst done  regime=%s  fg=%d  vix=%s  dxy=%s",
            macro_signal.regime.value,
            macro_signal.fear_greed_value,
            macro_signal.vix_level,
            macro_signal.dxy_trend,
        )
    except Exception:
        logger.exception("MacroAnalyst failed — aborting pipeline")
        return []

    # ── 5. Pre-screen ───────────────────────────────────────────────
    logger.info("Step 5/9: Pre-screening %d snapshots", len(snapshots))
    candidates = _pre_screen(snapshots, funnel)
    logger.info(
        "Pre-screen: %d/%d passed (killed %d)",
        len(candidates), len(snapshots), funnel.pre_screen_killed,
    )

    # ── 6. TradeAnalyst ─────────────────────────────────────────────
    logger.info("Step 6/9: Running TradeAnalyst on %d candidates", len(candidates))
    trade_analyst = TradeAnalyst()
    buy_proposals: list[TradeProposal] = []

    for snap in candidates:
        try:
            proposal = trade_analyst.run(snap, macro_signal)

            if proposal.action.value == "buy":
                buy_proposals.append(proposal)
                funnel.trade_analyst_buy += 1
                logger.info(
                    "TradeAnalyst BUY  %s  conf=%.2f  entry=%.4f–%.4f  "
                    "SL=%.4f  TP=%.4f",
                    proposal.symbol, proposal.confidence,
                    proposal.entry_zone_low, proposal.entry_zone_high,
                    proposal.stop_loss, proposal.take_profit,
                )
            else:
                funnel.trade_analyst_skip += 1
                logger.info(
                    "TradeAnalyst SKIP  %s  conf=%.2f  reason=%s",
                    proposal.symbol, proposal.confidence,
                    proposal.rationale[:100],
                )
        except Exception:
            logger.exception(
                "TradeAnalyst failed for %s — skipping", snap.symbol,
            )
            funnel.trade_analyst_error += 1

    logger.info(
        "TradeAnalyst done  buy=%d  skip=%d  error=%d",
        funnel.trade_analyst_buy, funnel.trade_analyst_skip,
        funnel.trade_analyst_error,
    )

    # ── 7. Python validation ────────────────────────────────────────
    logger.info("Step 7/9: Validating %d BUY proposals", len(buy_proposals))
    valid_proposals: list[TradeProposal] = []
    validated_rr: dict[str, float] = {}

    for proposal in buy_proposals:
        result = validate_proposal(proposal)
        if result.valid:
            valid_proposals.append(proposal)
            validated_rr[proposal.symbol] = result.reward_risk_ratio
            funnel.validation_passed += 1
        else:
            funnel.validation_failed += 1
            logger.info("Validation FAIL  %s", result.reason)

    logger.info(
        "Validation done  passed=%d  failed=%d",
        funnel.validation_passed, funnel.validation_failed,
    )

    if not valid_proposals:
        logger.warning("No valid proposals after validation")
        _finalize_pipeline(
            [], funnel, today, sentiment_snapshot, dry_run, universe,
            macro_regime=macro_signal.regime.value,
        )
        return []

    # ── 8. RiskReviewer ─────────────────────────────────────────────
    logger.info("Step 8/9: Running RiskReviewer on %d valid proposals", len(valid_proposals))
    risk_reviewer = RiskReviewer()

    # Get open signals for portfolio context
    open_signals: list[dict] = []
    if not dry_run:
        try:
            with SignalLogger() as sl:
                open_signals = sl.get_active_signals()
        except Exception:
            logger.warning("Could not fetch open signals for portfolio context")

    reviewed = risk_reviewer.run(valid_proposals, macro_signal, open_signals)

    # ── Build PlaybookEntries from proposals + reviews ──────────────
    entries: list[PlaybookEntry] = []
    proposal_map = {p.symbol: p for p in valid_proposals}

    for review in reviewed:
        proposal = proposal_map.get(review.symbol)
        if not proposal:
            continue

        verdict = (
            PlaybookVerdict.APPROVED
            if review.verdict == ReviewVerdict.APPROVE
            else PlaybookVerdict.REJECTED
        )

        # Conviction → risk pct mapping
        risk_pct = 0.02 if review.conviction == ConvictionTier.HIGH else 0.01

        rr = validated_rr[review.symbol]  # must exist — only validated proposals reach here

        entry = PlaybookEntry(
            symbol=proposal.symbol,
            timeframe=proposal.timeframe,
            report_date=today,
            verdict=verdict,
            verdict_reasoning=review.reasoning,
            direction=proposal.direction,
            entry_zone_low=proposal.entry_zone_low,
            entry_zone_high=proposal.entry_zone_high,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            reward_risk_ratio=rr,
            confidence_score=proposal.confidence,
            conviction=review.conviction,
            suggested_risk_pct=risk_pct,
            rationale=proposal.rationale,
            risk_notes=review.risk_notes,
            rank=1,  # will be re-ranked below
        )
        entries.append(entry)

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    rejected = [e for e in entries if e.verdict != PlaybookVerdict.APPROVED]
    funnel.reviewer_approved = len(approved)
    funnel.reviewer_rejected = len(rejected)

    logger.info(
        "RiskReviewer done  approved=%d  rejected=%d",
        len(approved), len(rejected),
    )

    # ── Signal cap — publish top N by confidence ──────────────────
    max_signals = risk_profile.get("max_daily_signals", 3)
    if len(approved) > max_signals:
        approved.sort(key=lambda e: e.confidence_score, reverse=True)
        funnel.signal_cap_killed = len(approved) - max_signals
        approved = approved[:max_signals]
        logger.info(
            "Signal cap: kept top %d, dropped %d",
            max_signals, funnel.signal_cap_killed,
        )

    # Re-rank (1 = highest confidence)
    approved.sort(key=lambda e: e.confidence_score, reverse=True)
    for i, entry in enumerate(approved, 1):
        approved[i - 1] = entry.model_copy(update={"rank": i})

    # ── 9. Persist + notify ───────────────────────────────────────
    _finalize_pipeline(
        approved, funnel, today, sentiment_snapshot, dry_run, universe,
        macro_regime=macro_signal.regime.value,
    )

    logger.info("=== Pipeline complete ===")
    return approved


def _finalize_pipeline(
    approved: list[PlaybookEntry],
    funnel: FunnelTracker,
    today: str,
    sentiment_snapshot: object,
    dry_run: bool,
    universe: Universe,
    macro_regime: str | None = None,
) -> None:
    """Log funnel, persist signals, post to Discord."""
    logger.info("Step 9/9: Logging signals and posting to Discord")

    if dry_run:
        funnel.published = len(approved)
        funnel_header = f"Pipeline Funnel — {today}"
        logger.info("%s\n%s", funnel_header, "\n".join(funnel.summary_lines()))
        logger.info("DRY RUN — skipping SQLite + Discord")
        for e in approved:
            logger.info(
                "  [DRY] %s %s  %s  conviction=%s  RR=%.1f  risk=%.0f%%",
                e.symbol, e.timeframe, e.direction.value.upper(),
                e.conviction.upper(), e.reward_risk_ratio,
                e.suggested_risk_pct * 100,
            )
        return

    # Log to SQLite
    trace_id = None
    if langfuse_context is not None:
        try:
            trace_id = langfuse_context.get_current_trace_id()
        except Exception:
            pass

    with SignalLogger() as signal_logger:
        new_signals: list[PlaybookEntry] = []
        for entry in approved:
            signal_id = signal_logger.log(
                entry,
                langfuse_trace_id=trace_id,
                asset_class=universe.asset_class_for(entry.symbol),
            )
            if signal_id:
                logger.info("Logged signal %s", signal_id)
                new_signals.append(entry)
            else:
                logger.info(
                    "Signal %s %s already exists — skipping",
                    entry.symbol, entry.report_date,
                )

        funnel.published = len(new_signals)

        # Log funnel to pipeline_runs table
        fg_val = getattr(sentiment_snapshot, "fear_greed_value", None)
        signal_logger.log_pipeline_run(
            today, funnel, fear_greed_value=fg_val, macro_regime=macro_regime,
        )

        open_count = signal_logger.open_signal_count()

    # Log funnel summary (after DB logging so published count is accurate)
    funnel_header = f"Pipeline Funnel — {today}"
    logger.info("%s\n%s", funnel_header, "\n".join(funnel.summary_lines()))

    # Post to Discord
    notifier = DiscordNotifier()
    if new_signals:
        posted = notifier.post(new_signals, open_count=open_count, report_date=today)
        if posted:
            logger.info(
                "Discord embed posted  signals=%d  open=%d",
                len(new_signals), open_count,
            )
    else:
        logger.info("No new signals to post")

    # Post funnel summary to Discord
    notifier.post_funnel(today, funnel)


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
        help="Specific symbols to analyse (default: full universe from config)",
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
    parser.add_argument(
        "--asset-class",
        default=None,
        choices=["crypto", "equity", "etf", "commodity"],
        help="Run pipeline for a single asset class only",
    )
    args = parser.parse_args(argv)
    # Convert empty list to None so run_pipeline uses config
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
        entries = run_pipeline(
            symbols=args.pairs,
            dry_run=args.dry_run,
            asset_class=args.asset_class,
        )
    except Exception as exc:
        logger.exception("Pipeline crashed")
        if not args.dry_run:
            try:
                notifier = DiscordNotifier()
                notifier.post_error(exc)
            except Exception:
                logger.exception("Failed to post error alert to Discord")
        sys.exit(1)

    print(f"\nDone. {len(entries)} approved signals.")


if __name__ == "__main__":
    main()
