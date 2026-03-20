"""
Outcome checker — walks 4h candles to resolve PENDING/OPEN signals.

Standalone script that runs independently from the main LLM pipeline.
No LLM calls, no expensive agent invocations — just candle data + DB updates.

Signal lifecycle:
  PENDING → OPEN:    candle low ≤ entry_high (price entered entry zone)
  OPEN    → HIT_TP:  candle high ≥ take_profit
  OPEN    → HIT_SL:  candle low ≤ stop_loss (assumed first on same-candle ambiguity)
  any     → EXPIRED: 14 calendar days with no resolution

Usage::

    python -m crypto_swing_copilot.check_outcomes          # check all active signals
    python -m crypto_swing_copilot.check_outcomes --dry-run # preview without DB writes
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from crypto_swing_copilot.data.market_data import MarketDataFetcher
from crypto_swing_copilot.db.signal_logger import SignalLogger

logger = logging.getLogger(__name__)

# Outcome checking always uses 4h candles regardless of signal timeframe
_CHECK_TIMEFRAME = "4h"

# Signals unresolved after this many days are marked EXPIRED
_EXPIRY_DAYS = 14


def check_outcomes(
    db_path: Path | str | None = None,
    fetcher: MarketDataFetcher | None = None,
    expiry_days: int = _EXPIRY_DAYS,
    dry_run: bool = False,
) -> dict:
    """Walk 4h candles for all active signals and update their status.

    Parameters
    ----------
    db_path:
        Override DB path (useful for testing).
    fetcher:
        Override MarketDataFetcher (useful for testing).
    expiry_days:
        Days after which unresolved signals expire.
    dry_run:
        If True, log what would happen but don't write to DB.

    Returns
    -------
    dict
        Summary: ``{"checked": N, "entered": N, "tp_hit": N, "sl_hit": N, "expired": N}``.
    """
    sig_logger = SignalLogger(db_path)
    if fetcher is None:
        fetcher = MarketDataFetcher()

    active = sig_logger.get_active_signals()
    if not active:
        logger.info("No active signals to check")
        return {"checked": 0, "entered": 0, "tp_hit": 0, "sl_hit": 0, "expired": 0}

    logger.info("Checking outcomes for %d active signals", len(active))

    summary = {"checked": len(active), "entered": 0, "tp_hit": 0, "sl_hit": 0, "expired": 0}

    # Group signals by pair to avoid re-fetching the same candles
    pairs = {s["pair"] for s in active}
    candle_cache: dict[str, pd.DataFrame] = {}

    for pair in pairs:
        try:
            candle_cache[pair] = fetcher.fetch_ohlcv(pair, _CHECK_TIMEFRAME)
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

        if not dry_run:
            sig_logger.update_signal(signal["signal_id"], **result)

        status = result.get("status", "")
        if status == "OPEN":
            summary["entered"] += 1
        elif status == "HIT_TP":
            summary["tp_hit"] += 1
        elif status == "HIT_SL":
            summary["sl_hit"] += 1
        elif status == "EXPIRED":
            summary["expired"] += 1

        logger.info(
            "Signal %s → %s  pair=%s  outcome_price=%s",
            signal["signal_id"], status, pair, result.get("outcome_price"),
        )

    logger.info(
        "Outcome check complete  checked=%d  entered=%d  tp=%d  sl=%d  expired=%d",
        summary["checked"], summary["entered"],
        summary["tp_hit"], summary["sl_hit"], summary["expired"],
    )
    return summary


def _process_signal(
    signal: dict,
    candles: pd.DataFrame,
    expiry_days: int,
) -> dict | None:
    """Walk candles chronologically and determine signal outcome.

    Returns
    -------
    dict | None
        Update dict for ``SignalLogger.update_signal()``, or None if no change.
    """
    created_at = datetime.fromisoformat(signal["created_at"])
    created_ts = int(created_at.timestamp() * 1000)
    now = datetime.now(timezone.utc)

    entry_high = signal["entry_high"]
    entry_low = signal["entry_low"]
    stop_loss = signal["stop_loss"]
    take_profit = signal["take_profit"]
    status = signal["status"]

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

    # Entry price for MAE/MFE calculation (worst-case fill for LONG)
    entry_price = entry_high

    # Track MAE (lowest low) and MFE (highest high) while OPEN
    mae_price = entry_price
    mfe_price = entry_price

    for row in df.itertuples(index=False):
        candle_low = row.low
        candle_high = row.high
        candle_ts = datetime.fromtimestamp(row.timestamp / 1000, tz=timezone.utc)

        if status == "PENDING":
            # Check if price entered entry zone
            if candle_low <= entry_high:
                status = "OPEN"
                entered_at = candle_ts.isoformat()
                # Don't return yet — continue walking to check TP/SL in same candle
            else:
                # Check expiry for PENDING signals too
                if (candle_ts - created_at) >= timedelta(days=expiry_days):
                    return {
                        "status": "EXPIRED",
                        "outcome_date": candle_ts.isoformat(),
                    }
                continue

        if status == "OPEN":
            # Track MAE/MFE
            mae_price = min(mae_price, candle_low)
            mfe_price = max(mfe_price, candle_high)

            # Check SL first (conservative — on same-candle ambiguity, SL wins)
            if candle_low <= stop_loss:
                return {
                    "status": "HIT_SL",
                    "entered_at": signal.get("entered_at") or entered_at,  # type: ignore[possibly-undefined]
                    "outcome_price": stop_loss,
                    "outcome_date": candle_ts.isoformat(),
                    "max_adverse_excursion": mae_price,
                    "max_favorable_excursion": mfe_price,
                }

            if candle_high >= take_profit:
                return {
                    "status": "HIT_TP",
                    "entered_at": signal.get("entered_at") or entered_at,  # type: ignore[possibly-undefined]
                    "outcome_price": take_profit,
                    "outcome_date": candle_ts.isoformat(),
                    "max_adverse_excursion": mae_price,
                    "max_favorable_excursion": mfe_price,
                }

            # Check expiry
            if (candle_ts - created_at) >= timedelta(days=expiry_days):
                return {
                    "status": "EXPIRED",
                    "entered_at": signal.get("entered_at") or entered_at,  # type: ignore[possibly-undefined]
                    "outcome_date": candle_ts.isoformat(),
                    "max_adverse_excursion": mae_price,
                    "max_favorable_excursion": mfe_price,
                }

    # Signal transitioned to OPEN but no TP/SL/expiry yet
    if status == "OPEN" and signal["status"] == "PENDING":
        return {
            "status": "OPEN",
            "entered_at": entered_at,  # type: ignore[possibly-undefined]
            "max_adverse_excursion": mae_price,
            "max_favorable_excursion": mfe_price,
        }

    # Still OPEN from before, just update MAE/MFE
    if status == "OPEN" and signal["status"] == "OPEN" and (
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
    console.print("\n[bold cyan]crypto-swing-copilot · Outcome Checker[/bold cyan]\n")

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        console.print("[yellow]DRY RUN — no DB writes[/yellow]\n")

    summary = check_outcomes(dry_run=dry_run)

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Checked:  {summary['checked']}")
    console.print(f"  Entered:  {summary['entered']}")
    console.print(f"  TP hit:   {summary['tp_hit']}")
    console.print(f"  SL hit:   {summary['sl_hit']}")
    console.print(f"  Expired:  {summary['expired']}")
    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    _cli_main()
