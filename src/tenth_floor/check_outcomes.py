"""
Outcome checker — walks candles to resolve OPEN signals.

Standalone script that runs independently from the main LLM pipeline.
No LLM calls, no expensive agent invocations — just candle data + DB updates.

Signal lifecycle:
  OPEN → HIT_TP:  candle high ≥ take_profit
  OPEN → HIT_SL:  candle low ≤ stop_loss (assumed first on same-candle ambiguity)
  OPEN → EXPIRED: 14 calendar days with no resolution

A signal is born OPEN at the snapshot price the operator saw on the
dashboard. There is no PENDING tier and no entry zone — see migration
003_drop_entry_zone_and_pending for context.

Usage::

    python -m tenth_floor.check_outcomes          # check all active signals
    python -m tenth_floor.check_outcomes --dry-run # preview without DB writes
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from tenth_floor.data.market_data import MarketDataFetcher
from tenth_floor.data.yfinance_data import YFinanceDataFetcher
from tenth_floor.db.signal_logger import SignalLogger
from tenth_floor.events import EventType, event_bus
from tenth_floor.notifications.notion_journal import update_signal_outcome
from tenth_floor.universe import load_universe

logger = logging.getLogger(__name__)

# Signals unresolved after this many days are marked EXPIRED
_EXPIRY_DAYS = 14


def check_outcomes(
    db_path: Path | str | None = None,
    fetcher: MarketDataFetcher | None = None,
    expiry_days: int = _EXPIRY_DAYS,
    dry_run: bool = False,
    run_id: str | None = None,
) -> dict:
    """Walk candles for all active signals and update their status.

    Uses the universe config to route each symbol to the correct data
    source (ccxt for crypto, yfinance for equities/ETFs/commodities)
    and check timeframe (4h for crypto, 1d for equities).

    Parameters
    ----------
    db_path:
        Override DB path (useful for testing).
    fetcher:
        Override MarketDataFetcher for crypto (useful for testing).
    expiry_days:
        Days after which unresolved signals expire.
    dry_run:
        If True, log what would happen but don't write to DB.
    run_id:
        When provided, emit ``outcome_check_*`` and ``outcome_resolved``
        events to the shared event bus under this run id. The API layer
        passes the pipeline's run id so the dashboard sees the pipeline
        and the outcome check as a single live stream.

    Returns
    -------
    dict
        Summary: ``{"checked": N, "entered": N, "tp_hit": N, "sl_hit": N, "expired": N}``.
    """
    sig_logger = SignalLogger(db_path)
    if fetcher is None:
        fetcher = MarketDataFetcher()

    universe = load_universe()
    yf_fetcher = YFinanceDataFetcher()

    active = sig_logger.get_active_signals()

    if run_id is not None:
        event_bus.emit(
            run_id,
            EventType.OUTCOME_CHECK_STARTED,
            {"active_count": len(active), "dry_run": dry_run},
        )

    if not active:
        logger.info("No active signals to check")
        summary = {"checked": 0, "tp_hit": 0, "sl_hit": 0, "expired": 0}
        if run_id is not None:
            event_bus.emit(run_id, EventType.OUTCOME_CHECK_COMPLETE, {"summary": summary})
        return summary

    logger.info("Checking outcomes for %d active signals", len(active))

    summary = {"checked": len(active), "tp_hit": 0, "sl_hit": 0, "expired": 0}

    # Group signals by pair to avoid re-fetching the same candles
    pairs = {s["pair"] for s in active}
    candle_cache: dict[str, pd.DataFrame] = {}

    for pair in pairs:
        try:
            try:
                data_source = universe.data_source_for(pair)
                check_tf = universe.class_config(universe.asset_class_for(pair)).check_timeframe
            except KeyError:
                # Symbol not in current universe (legacy signal) — default to ccxt/4h
                data_source = "ccxt"
                check_tf = "4h"
            if data_source == "yfinance":
                candle_cache[pair] = yf_fetcher.fetch_ohlcv(pair, check_tf)
            else:
                candle_cache[pair] = fetcher.fetch_ohlcv(pair, check_tf)
        except Exception:
            logger.exception("Failed to fetch candles for %s — skipping", pair)

    for signal in active:
        pair = signal["pair"]
        if pair not in candle_cache or candle_cache[pair].empty:
            logger.warning("No candle data for %s — skipping signal %s", pair, signal["signal_id"])
            continue

        result = _process_signal(signal, candle_cache[pair], expiry_days)

        if result is None:
            continue  # no state change

        status = result.get("status", "")

        if not dry_run:
            sig_logger.update_signal(signal["signal_id"], **result)
            if status in ("HIT_TP", "HIT_SL", "EXPIRED"):
                notion_page_id = signal.get("notion_page_id")
                if notion_page_id:
                    update_signal_outcome(
                        notion_page_id,
                        status=status,
                        outcome_price=result.get("outcome_price"),
                        outcome_date=result.get("outcome_date"),
                        mae=result.get("max_adverse_excursion"),
                        mfe=result.get("max_favorable_excursion"),
                        entry_price=signal.get("entry_price"),
                    )

        if status == "HIT_TP":
            summary["tp_hit"] += 1
        elif status == "HIT_SL":
            summary["sl_hit"] += 1
        elif status == "EXPIRED":
            summary["expired"] += 1

        logger.info(
            "Signal %s → %s  pair=%s  outcome_price=%s",
            signal["signal_id"], status, pair, result.get("outcome_price"),
        )

        if run_id is not None:
            event_bus.emit(
                run_id,
                EventType.OUTCOME_RESOLVED,
                {
                    "signal_id": signal["signal_id"],
                    "pair": pair,
                    "status": status,
                    "outcome_price": result.get("outcome_price"),
                    "outcome_date": result.get("outcome_date"),
                },
            )

    logger.info(
        "Outcome check complete  checked=%d  tp=%d  sl=%d  expired=%d",
        summary["checked"],
        summary["tp_hit"], summary["sl_hit"], summary["expired"],
    )

    if run_id is not None:
        event_bus.emit(run_id, EventType.OUTCOME_CHECK_COMPLETE, {"summary": summary})

    return summary


def _process_signal(
    signal: dict,
    candles: pd.DataFrame,
    expiry_days: int,
) -> dict | None:
    """Walk candles chronologically and determine signal outcome.

    The signal opens at ``entry_price`` on its creation date. We walk
    forward looking for SL or TP first, with SL winning on same-candle
    ambiguity (conservative).

    Returns
    -------
    dict | None
        Update dict for ``SignalLogger.update_signal()``, or None if no change.
    """
    created_at = datetime.fromisoformat(signal["created_at"])
    created_ts = int(created_at.timestamp() * 1000)
    now = datetime.now(UTC)

    entry_price = signal["entry_price"]
    stop_loss = signal["stop_loss"]
    take_profit = signal["take_profit"]

    # Filter candles to those after signal creation
    if candles.empty:
        df = candles
    else:
        df = candles[candles["timestamp"] >= created_ts].sort_values("timestamp")

    if df.empty:
        # No new candles since signal creation — check expiry only
        if (now - created_at) >= timedelta(days=expiry_days):
            return {
                "status": "EXPIRED",
                "outcome_date": now.isoformat(),
            }
        return None

    # Track MAE (lowest low) and MFE (highest high) while OPEN
    mae_price = entry_price
    mfe_price = entry_price

    for row in df.itertuples(index=False):
        candle_low = row.low
        candle_high = row.high
        candle_ts = datetime.fromtimestamp(row.timestamp / 1000, tz=UTC)

        mae_price = min(mae_price, candle_low)
        mfe_price = max(mfe_price, candle_high)

        # Check SL first (conservative — on same-candle ambiguity, SL wins)
        if candle_low <= stop_loss:
            return {
                "status": "HIT_SL",
                "outcome_price": stop_loss,
                "outcome_date": candle_ts.isoformat(),
                "max_adverse_excursion": mae_price,
                "max_favorable_excursion": mfe_price,
            }

        if candle_high >= take_profit:
            return {
                "status": "HIT_TP",
                "outcome_price": take_profit,
                "outcome_date": candle_ts.isoformat(),
                "max_adverse_excursion": mae_price,
                "max_favorable_excursion": mfe_price,
            }

        if (candle_ts - created_at) >= timedelta(days=expiry_days):
            return {
                "status": "EXPIRED",
                "outcome_date": candle_ts.isoformat(),
                "max_adverse_excursion": mae_price,
                "max_favorable_excursion": mfe_price,
            }

    # Still OPEN — only emit an update if MAE/MFE moved.
    if (
        mae_price != signal.get("max_adverse_excursion")
        or mfe_price != signal.get("max_favorable_excursion")
    ):
        return {
            "status": "OPEN",
            "max_adverse_excursion": mae_price,
            "max_favorable_excursion": mfe_price,
        }

    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Run outcome checker from the command line."""
    import sys

    from rich.console import Console

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    console = Console()
    console.print("\n[bold cyan]the-tenth-floor · Outcome Checker[/bold cyan]\n")

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        console.print("[yellow]DRY RUN — no DB writes[/yellow]\n")

    summary = check_outcomes(dry_run=dry_run)

    console.print("\n[bold]Results:[/bold]")
    console.print(f"  Checked:  {summary['checked']}")
    console.print(f"  TP hit:   {summary['tp_hit']}")
    console.print(f"  SL hit:   {summary['sl_hit']}")
    console.print(f"  Expired:  {summary['expired']}")
    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    _cli_main()
