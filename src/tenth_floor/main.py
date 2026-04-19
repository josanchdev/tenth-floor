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
    python -m tenth_floor.main --dry-run           # skip SQLite writes
    python -m tenth_floor.main --asset-class crypto # crypto only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

from tenth_floor.agents.base import load_risk_profile, set_active_profile
from tenth_floor.agents.macro_analyst import MacroAnalyst
from tenth_floor.agents.risk_reviewer import RiskReviewer
from tenth_floor.agents.trade_analyst import TradeAnalyst
from tenth_floor.data.market_data import MarketDataFetcher
from tenth_floor.data.models import (
    ConvictionTier,
    MacroSignal,
    PlaybookEntry,
    PlaybookVerdict,
    ReviewVerdict,
    SetupAction,
    TradeProposal,
)
from tenth_floor.data.sentiment import SentimentFetcher
from tenth_floor.data.yfinance_data import YFinanceDataFetcher
from tenth_floor.db.signal_logger import SignalLogger
from tenth_floor.events import EventType, event_bus
from tenth_floor.features.pair_snapshot import SnapshotBuilder
from tenth_floor.notifications.discord_notifier import publish_playbook
from tenth_floor.notifications.notion_journal import create_signal_entry
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


class PipelineCancelled(Exception):
    """Raised when the dashboard asks the run manager to cancel an in-flight run.

    Cooperative cancellation — ``run_pipeline`` checks the ``cancel_event``
    at phase boundaries and inside the per-asset TradeAnalyst loop. Worst-case
    latency is one in-flight LLM call (30–60s on a 3090).
    """


def _check_cancel(cancel_event: threading.Event | None) -> None:
    """Raise :class:`PipelineCancelled` if cancellation has been requested."""
    if cancel_event is not None and cancel_event.is_set():
        raise PipelineCancelled()


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


def _macro_alignment_bonus(
    asset_class: str | None,
    macro: MacroSignal,
) -> float:
    """Return a ranking bonus based on macro alignment for this asset class.

    The bonus is added to the entry's confidence score for ranking purposes
    only — it does not modify the published confidence number. It exists so
    that when the signal cap activates, a high-confidence signal in a
    macro-tailwind asset class outranks an equally-confident signal in a
    macro-headwind class.

    +0.10  outlook bullish
     0.00  outlook neutral / unknown
    -0.10  outlook bearish
    """
    if not asset_class:
        return 0.0
    for impact in macro.asset_class_impacts:
        if impact.asset_class.lower() == asset_class.lower():
            outlook = impact.outlook.lower()
            if "bull" in outlook:
                return 0.10
            if "bear" in outlook:
                return -0.10
            return 0.0
    return 0.0


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


def _resnap_published_entries(
    approved: list[PlaybookEntry],
    universe: Universe,
    run_id: str,
) -> list[PlaybookEntry]:
    """Refetch the live price for each survivor and re-anchor the entry.

    The original snapshot was taken before ~30 sequential LLM calls and
    can be many minutes stale. We only do this for the small set that
    will actually be persisted, so the extra ticker calls are cheap.

    If the live price has crossed SL or TP, or has pushed R:R below
    1.5, the entry is dropped — the trade is no longer the one the
    analyst proposed.
    """
    if not approved:
        return approved

    ccxt_fetcher: MarketDataFetcher | None = None
    yf_fetcher: YFinanceDataFetcher | None = None

    survivors: list[PlaybookEntry] = []
    for entry in approved:
        try:
            data_source = universe.data_source_for(entry.symbol)
        except KeyError:
            data_source = "ccxt"

        try:
            if data_source == "yfinance":
                if yf_fetcher is None:
                    yf_fetcher = YFinanceDataFetcher()
                live_price = yf_fetcher.fetch_last_price(entry.symbol)
            else:
                if ccxt_fetcher is None:
                    ccxt_fetcher = MarketDataFetcher()
                live_price = ccxt_fetcher.fetch_last_price(entry.symbol)
        except Exception:
            logger.exception(
                "Re-snap failed for %s — keeping original entry_price", entry.symbol,
            )
            survivors.append(entry)
            continue

        # Re-validate against the new price.
        proposal = TradeProposal(
            symbol=entry.symbol,
            timeframe=entry.timeframe,
            action=SetupAction.BUY,
            direction=entry.direction,
            entry_price=live_price,
            stop_loss=entry.stop_loss,
            take_profit=entry.take_profit,
            rationale=entry.rationale,
        )
        result = validate_proposal(proposal, current_price=live_price)
        if not result.valid:
            logger.warning(
                "Re-snap DROPPED  %s  old=%.4f  live=%.4f  reason=%s",
                entry.symbol, entry.entry_price, live_price, result.reason,
            )
            event_bus.emit(run_id, EventType.VALIDATION_RESULT, {
                "symbol": entry.symbol,
                "passed": False,
                "reason": f"resnap: {result.reason}",
            })
            continue

        drift_pct = (live_price - entry.entry_price) / entry.entry_price * 100
        logger.info(
            "Re-snap %s  old=%.4f  live=%.4f  drift=%+.2f%%  RR=%.2f",
            entry.symbol, entry.entry_price, live_price, drift_pct,
            result.reward_risk_ratio,
        )
        survivors.append(entry.model_copy(update={
            "entry_price": live_price,
            "reward_risk_ratio": result.reward_risk_ratio,
        }))

    return survivors


@lf_observe(name="daily_pipeline")
def run_pipeline(
    symbols: list[str] | None = None,
    *,
    dry_run: bool = False,
    asset_class: str | None = None,
    run_id: str | None = None,
    cancel_event: threading.Event | None = None,
) -> list[PlaybookEntry]:
    """Execute the full daily pipeline.

    Parameters
    ----------
    symbols:
        Override the universe. ``None`` uses ``config/universe.json``.
    dry_run:
        If ``True``, skip SQLite logging.
    asset_class:
        Filter universe to a single asset class (e.g. "crypto", "equity").
    run_id:
        Externally-generated run id used by the dashboard to route events
        to the right WebSocket. Generated automatically if not provided.
    cancel_event:
        Optional ``threading.Event`` that the pipeline checks at phase
        boundaries. When set, the current phase finishes its in-flight
        LLM call and then :class:`PipelineCancelled` is raised so the
        run manager can emit ``pipeline_cancelled``.

    Returns
    -------
    list[PlaybookEntry]
        All approved entries for today.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if run_id is None:
        run_id = event_bus.new_run_id()
    started_at = time.monotonic()

    logger.info("=== THE TENTH FLOOR — daily run %s (run_id=%s) ===", today, run_id)
    funnel = FunnelTracker()
    risk_profile = load_risk_profile()

    try:
        # ── Load universe ─────────────────────────────────────────────
        universe = load_universe()
        if symbols is None:
            symbols = universe.symbols(asset_class=asset_class)
        funnel.assets_in_universe = len(symbols)
        logger.info("Universe: %d symbols%s", len(symbols),
                    f" (asset_class={asset_class})" if asset_class else "")

        event_bus.emit(run_id, EventType.PIPELINE_STARTED, {
            "today": today,
            "total_assets": len(symbols),
            "asset_class": asset_class,
            "dry_run": dry_run,
            "max_daily_signals": risk_profile.get("max_daily_signals", 3),
        })

        return _run_pipeline_body(
            run_id=run_id,
            today=today,
            started_at=started_at,
            symbols=symbols,
            universe=universe,
            funnel=funnel,
            risk_profile=risk_profile,
            dry_run=dry_run,
            cancel_event=cancel_event,
        )
    except PipelineCancelled:
        # Cancellation is operator-initiated, not a crash. The runner
        # thread emits ``pipeline_cancelled`` — we just unwind cleanly.
        logger.info("Pipeline cancelled by operator (run_id=%s)", run_id)
        raise
    except Exception as exc:
        event_bus.emit(run_id, EventType.PIPELINE_ERROR, {
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        raise


def _run_pipeline_body(
    *,
    run_id: str,
    today: str,
    started_at: float,
    symbols: list[str],
    universe: Universe,
    funnel: FunnelTracker,
    risk_profile: dict,
    dry_run: bool,
    cancel_event: threading.Event | None = None,
) -> list[PlaybookEntry]:
    """Pipeline body — extracted so the outer wrapper can emit error events."""

    # ── 1. Fetch market data ────────────────────────────────────────
    _check_cancel(cancel_event)
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
    _check_cancel(cancel_event)
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
        event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {
            "published_count": 0,
            "funnel": funnel.summary_lines(),
            "elapsed_seconds": round(time.monotonic() - started_at, 2),
            "aborted": True,
            "abort_reason": "macro_analyst_failed",
        })
        return []

    event_bus.emit(run_id, EventType.MACRO_COMPLETE, {
        "regime": macro_signal.regime.value,
        "fear_greed_value": macro_signal.fear_greed_value,
        "vix_level": macro_signal.vix_level,
        "dxy_trend": macro_signal.dxy_trend,
        "asset_class_impacts": [
            {"asset_class": i.asset_class, "outlook": i.outlook, "reasoning": i.reasoning}
            for i in macro_signal.asset_class_impacts
        ],
        "regime_reasoning": macro_signal.regime_reasoning,
    })

    # ── 5. Pre-screen ───────────────────────────────────────────────
    logger.info("Step 5/9: Pre-screening %d snapshots", len(snapshots))
    candidates = _pre_screen(snapshots, funnel)
    logger.info(
        "Pre-screen: %d/%d passed (killed %d)",
        len(candidates), len(snapshots), funnel.pre_screen_killed,
    )

    # ── 6. TradeAnalyst ─────────────────────────────────────────────
    _check_cancel(cancel_event)
    logger.info("Step 6/9: Running TradeAnalyst on %d candidates", len(candidates))
    trade_analyst = TradeAnalyst()
    buy_proposals: list[TradeProposal] = []

    for snap in candidates:
        _check_cancel(cancel_event)
        try:
            proposal = trade_analyst.run(snap, macro_signal)

            if proposal.action.value == "buy":
                buy_proposals.append(proposal)
                funnel.trade_analyst_buy += 1
                logger.info(
                    "TradeAnalyst BUY  %s  entry=%.4f  SL=%.4f  TP=%.4f",
                    proposal.symbol,
                    proposal.entry_price,
                    proposal.stop_loss, proposal.take_profit,
                )
                event_bus.emit(run_id, EventType.ASSET_ANALYZED, {
                    "symbol": proposal.symbol,
                    "asset_class": universe.asset_class_for(proposal.symbol),
                    "action": "buy",
                    "entry_price": proposal.entry_price,
                    "stop_loss": proposal.stop_loss,
                    "take_profit": proposal.take_profit,
                    "rationale": proposal.rationale,
                })
            else:
                funnel.trade_analyst_skip += 1
                logger.info(
                    "TradeAnalyst SKIP  %s  reason=%s",
                    proposal.symbol,
                    proposal.rationale[:100],
                )
                event_bus.emit(run_id, EventType.ASSET_ANALYZED, {
                    "symbol": proposal.symbol,
                    "asset_class": universe.asset_class_for(proposal.symbol),
                    "action": "skip",
                    "rationale": proposal.rationale,
                })
        except Exception as exc:
            logger.exception(
                "TradeAnalyst failed for %s — skipping", snap.symbol,
            )
            funnel.trade_analyst_error += 1
            event_bus.emit(run_id, EventType.ASSET_ANALYZED, {
                "symbol": snap.symbol,
                "asset_class": universe.asset_class_for(snap.symbol),
                "action": "error",
                "error": str(exc),
            })

    logger.info(
        "TradeAnalyst done  buy=%d  skip=%d  error=%d",
        funnel.trade_analyst_buy, funnel.trade_analyst_skip,
        funnel.trade_analyst_error,
    )

    # ── 7. Python validation ────────────────────────────────────────
    logger.info("Step 7/9: Validating %d BUY proposals", len(buy_proposals))
    valid_proposals: list[TradeProposal] = []
    validated_rr: dict[str, float] = {}

    snap_by_symbol = {s.symbol: s for s in candidates}
    for proposal in buy_proposals:
        snap = snap_by_symbol.get(proposal.symbol)
        if snap is None:
            funnel.validation_failed += 1
            logger.warning("Validation FAIL  %s — no snapshot for proposal", proposal.symbol)
            continue
        result = validate_proposal(proposal, current_price=snap.current_price)
        if result.valid:
            valid_proposals.append(proposal)
            validated_rr[proposal.symbol] = result.reward_risk_ratio
            funnel.validation_passed += 1
            event_bus.emit(run_id, EventType.VALIDATION_RESULT, {
                "symbol": proposal.symbol,
                "passed": True,
                "reward_risk_ratio": result.reward_risk_ratio,
            })
        else:
            funnel.validation_failed += 1
            logger.info("Validation FAIL  %s", result.reason)
            event_bus.emit(run_id, EventType.VALIDATION_RESULT, {
                "symbol": proposal.symbol,
                "passed": False,
                "reason": result.reason,
            })

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
        event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {
            "published_count": 0,
            "funnel": funnel.summary_lines(),
            "elapsed_seconds": round(time.monotonic() - started_at, 2),
            "aborted": False,
        })
        return []

    # ── 8. RiskReviewer ─────────────────────────────────────────────
    _check_cancel(cancel_event)
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
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            reward_risk_ratio=rr,
            confidence_score=review.confidence,
            conviction=review.conviction,
            suggested_risk_pct=risk_pct,
            rationale=proposal.rationale,
            risk_notes=review.risk_notes,
            rank=1,  # will be re-ranked below
        )
        entries.append(entry)

        event_bus.emit(run_id, EventType.REVIEWER_DECISION, {
            "symbol": proposal.symbol,
            "verdict": verdict.value,
            "conviction": review.conviction.value if hasattr(review.conviction, "value") else str(review.conviction),
            "confidence": review.confidence,
            "reasoning": review.reasoning,
            "risk_notes": review.risk_notes,
        })

    approved = [e for e in entries if e.verdict == PlaybookVerdict.APPROVED]
    rejected = [e for e in entries if e.verdict != PlaybookVerdict.APPROVED]
    funnel.reviewer_approved = len(approved)
    funnel.reviewer_rejected = len(rejected)

    logger.info(
        "RiskReviewer done  approved=%d  rejected=%d",
        len(approved), len(rejected),
    )

    # ── Signal cap — rank by macro-adjusted confidence ────────────
    # Confidence alone is not enough: a 0.78 signal in a macro-tailwind
    # asset class is a better trade than a 0.80 signal fighting the macro.
    # We add a small bonus/penalty based on the MacroAnalyst's per-class
    # outlook for ranking purposes only — the published confidence is
    # untouched.
    def _ranking_score(entry: PlaybookEntry) -> float:
        ac = universe.asset_class_for(entry.symbol)
        return entry.confidence_score + _macro_alignment_bonus(ac, macro_signal)

    max_signals = risk_profile.get("max_daily_signals", 3)
    approved.sort(key=_ranking_score, reverse=True)

    session: list[PlaybookEntry] = []
    if len(approved) > max_signals:
        funnel.signal_cap_killed = len(approved) - max_signals
        session = approved[max_signals:]
        approved = approved[:max_signals]
        logger.info(
            "Signal cap: kept top %d as PUBLISHED, %d → SESSION (macro-aligned ranking)",
            max_signals, funnel.signal_cap_killed,
        )
        for d in session:
            logger.info(
                "  session %s  conf=%.2f  macro_adj=%+.2f",
                d.symbol, d.confidence_score,
                _macro_alignment_bonus(universe.asset_class_for(d.symbol), macro_signal),
            )

    # ── Re-snap entry_price for survivors ─────────────────────────
    # The pipeline started with a single OHLCV fetch and then ran ~30
    # sequential LLM calls. By the time we publish, that snapshot can
    # be 5-15 minutes stale. For the small set of survivors we refetch
    # the live price, then re-validate against SL/TP. If the market
    # has moved past the stop or invalidated R:R, drop the entry.
    approved = _resnap_published_entries(approved, universe, run_id)

    # Assign ranks (1 = top of macro-adjusted ranking) AFTER any drops.
    for i, entry in enumerate(approved, 1):
        approved[i - 1] = entry.model_copy(update={"rank": i})

    # ── 9. Persist + notify ───────────────────────────────────────
    _finalize_pipeline(
        approved, funnel, today, sentiment_snapshot, dry_run, universe,
        macro_regime=macro_signal.regime.value,
        session=session,
    )

    event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {
        "published_count": funnel.published,
        "approved_count": len(approved),
        "funnel": funnel.summary_lines(),
        "elapsed_seconds": round(time.monotonic() - started_at, 2),
        "aborted": False,
    })

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
    session: list[PlaybookEntry] | None = None,
) -> None:
    """Log funnel, persist signals to SQLite."""
    logger.info("Step 9/9: Logging signals to SQLite")
    session = session or []

    if dry_run:
        funnel.published = len(approved)
        funnel_header = f"Pipeline Funnel — {today}"
        logger.info("%s\n%s", funnel_header, "\n".join(funnel.summary_lines()))
        logger.info("DRY RUN — skipping SQLite")
        for e in approved:
            logger.info(
                "  [DRY] %s %s  %s  conviction=%s  RR=%.1f  risk=%.0f%%",
                e.symbol, e.timeframe, e.direction.value.upper(),
                e.conviction.upper(), e.reward_risk_ratio,
                e.suggested_risk_pct * 100,
            )
        for e in session:
            logger.info("  [DRY] SESSION %s conf=%.2f", e.symbol, e.confidence_score)
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
                tier="PUBLISHED",
            )
            if signal_id:
                logger.info("Logged signal %s", signal_id)
                new_signals.append(entry)
                notion_page_id = create_signal_entry(
                    entry,
                    signal_id=signal_id,
                    asset_class=universe.asset_class_for(entry.symbol),
                    macro_regime=macro_regime,
                )
                if notion_page_id:
                    signal_logger.update_signal(signal_id, notion_page_id=notion_page_id)
            else:
                logger.info(
                    "Signal %s %s already exists — skipping",
                    entry.symbol, entry.report_date,
                )

        for entry in session:
            session_id = signal_logger.log(
                entry,
                langfuse_trace_id=trace_id,
                asset_class=universe.asset_class_for(entry.symbol),
                tier="SESSION",
            )
            if session_id:
                logger.info("Logged session signal %s", session_id)

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

    if new_signals:
        logger.info("Published %d signals  open=%d", len(new_signals), open_count)
        publish_playbook(
            new_signals, report_date=today, macro_regime=macro_regime,
        )
    else:
        logger.info("No new signals to publish")


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
        help="Run pipeline but skip SQLite logging",
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
    except Exception:
        logger.exception("Pipeline crashed")
        sys.exit(1)

    print(f"\nDone. {len(entries)} approved signals.")


if __name__ == "__main__":
    main()
