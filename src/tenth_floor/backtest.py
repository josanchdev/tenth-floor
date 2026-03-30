"""
Historical replay backtester — replay deterministic gates against cached OHLCV.

No LLM calls.  Uses ``trend_score`` (7-signal agreement) to simulate both
the trend-regime classification and confidence gating that the live pipeline
delegates to QuantAgent + RiskAgent.

Fetches historical Fear & Greed data from alternative.me to simulate the
capitulation bypass gate (F&G rising from extreme fear + RSI divergence).

Usage::

    python -m tenth_floor.backtest --days 90 --profile validation
    python -m tenth_floor.backtest --days 30 --min-rr 1.8
    python -m tenth_floor.backtest --days 60 --no-volume-gate
    python -m tenth_floor.backtest --days 90 --no-capitulation  # disable bypass
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

from tenth_floor.agents.base import load_risk_profile, set_active_profile
from tenth_floor.data.market_data import MarketDataFetcher
from tenth_floor.features.pair_snapshot import SnapshotBuilder

logger = logging.getLogger(__name__)

# Trend-score thresholds for simulating QuantAgent regime classification.
# These approximate what the LLM would classify based on indicator consensus.
_STRONG_DOWNTREND_MAX = 0.15  # 0–1/7 signals bullish
_DOWNTREND_MAX = 0.30         # ~2/7 signals bullish


@dataclass
class DayResult:
    """Funnel counters for a single simulated day."""

    date: str
    pairs_analyzed: int = 0
    killed_trend_gate: int = 0
    capitulation_bypassed: int = 0
    killed_strategy_skip: int = 0
    killed_volume_gate: int = 0
    killed_rs_gate: int = 0
    killed_confidence_gate: int = 0
    killed_rr_gate: int = 0
    killed_btc_corr_gate: int = 0
    killed_signal_cap: int = 0
    proposals: int = 0
    approved: int = 0
    fg_value: int | None = None
    fg_rising_from_extreme: bool = False


# ---------------------------------------------------------------------------
# Historical Fear & Greed data
# ---------------------------------------------------------------------------

_FG_API_URL = "https://api.alternative.me/fng/"
_FG_CACHE: dict[str, int] | None = None  # date-string → value


def _fetch_fg_history(days: int) -> dict[str, int]:
    """Fetch historical Fear & Greed values from alternative.me.

    Returns a dict mapping ``"YYYY-MM-DD"`` → F&G value.
    On failure, returns an empty dict (capitulation bypass is simply disabled).
    """
    global _FG_CACHE
    if _FG_CACHE is not None:
        return _FG_CACHE

    # Request extra days for the 7-day trend lookback window
    limit = days + 10
    url = f"{_FG_API_URL}?limit={limit}&format=json"
    logger.info("Fetching %d days of historical F&G data...", limit)

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "the-tenth-floor-backtest/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        logger.warning("F&G API unavailable: %s — capitulation bypass disabled", exc)
        _FG_CACHE = {}
        return _FG_CACHE

    entries = data.get("data", [])
    result: dict[str, int] = {}
    for entry in entries:
        try:
            ts = int(entry["timestamp"])
            val = int(entry["value"])
            date_str = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
            result[date_str] = val
        except (KeyError, ValueError):
            continue

    logger.info("Loaded F&G history: %d days", len(result))
    _FG_CACHE = result
    return _FG_CACHE


def _check_fg_rising_from_extreme(
    fg_history: dict[str, int],
    sim_date: str,
) -> tuple[bool, int | None]:
    """Check if F&G is rising from extreme fear on a given date.

    Replicates the exact logic from ``main.py``:
    - Current F&G < 25 (extreme fear)
    - Current value > trough of last 7 days
    - Delta from trough >= 3 points

    Returns ``(is_rising, current_fg_value)``.
    """
    # Build 7-day trend ending at sim_date
    dt = datetime.strptime(sim_date, "%Y-%m-%d").replace(tzinfo=UTC)
    trend: list[int] = []
    for offset in range(7):
        d = dt - timedelta(days=offset)
        d_str = d.strftime("%Y-%m-%d")
        if d_str in fg_history:
            trend.append(fg_history[d_str])

    if len(trend) < 3:
        return False, trend[0] if trend else None

    current = trend[0]
    if current >= 25:
        return False, current

    trough = min(trend)
    if current > trough and (current - trough) >= 3:
        return True, current

    return False, current


def _compute_price_levels(
    snapshot: object,
    *,
    sl_mult: float,
    min_rr: float,
) -> dict:
    """Replicate StrategyAgent._compute_price_levels() without importing it.

    Kept self-contained so the backtester has zero LLM dependencies.
    Entry at market price, SL below structural support, TP at resistance.
    """
    price = snapshot.current_price  # type: ignore[union-attr]
    ind = snapshot.indicators  # type: ignore[union-attr]
    atr = ind.atr_14 or (price * 0.02)

    # Dynamic precision based on price magnitude
    from tenth_floor.agents.strategy_agent import StrategyAgent

    decimals = StrategyAgent._price_decimals(price)

    supports = ind.support_levels
    resistances = ind.resistance_levels

    # Entry zone: anchored to current market price
    entry_high = round(price, decimals)
    entry_low = round(price - atr * 0.25, decimals)
    entry_mid = (entry_low + entry_high) / 2

    # Stop-loss: below nearest structural support
    nearby_supports = [s for s in supports if s < price]
    if nearby_supports:
        anchor_support = nearby_supports[-1]
        stop_loss = round(anchor_support - atr * 0.3, decimals)
    else:
        stop_loss = round(entry_mid - atr * sl_mult, decimals)

    actual_risk = entry_mid - stop_loss
    if actual_risk <= 0:
        stop_loss = round(entry_mid - atr * sl_mult, decimals)
        actual_risk = entry_mid - stop_loss

    # Take-profit: nearest resistance above price
    above_resistances = [r for r in resistances if r > price]

    if above_resistances:
        candidate_tp = above_resistances[0]
        candidate_rr = (candidate_tp - entry_mid) / actual_risk if actual_risk > 0 else 0
        if candidate_rr >= min_rr:
            take_profit = round(candidate_tp, decimals)
        else:
            take_profit = round(entry_mid + actual_risk * min_rr, decimals)
    else:
        take_profit = round(entry_mid + actual_risk * min_rr, decimals)

    reward = take_profit - entry_mid
    rr_ratio = round(reward / actual_risk, 2) if actual_risk > 0 else min_rr

    return {
        "entry_zone_low": entry_low,
        "entry_zone_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reward_risk_ratio": rr_ratio,
    }


def replay(
    *,
    days: int = 90,
    min_rr_override: float | None = None,
    min_confidence_override: float | None = None,
    max_signals_override: int | None = None,
    disable_volume_gate: bool = False,
    disable_rs_gate: bool = False,
    disable_capitulation: bool = False,
) -> list[DayResult]:
    """Replay the deterministic pipeline gates against cached OHLCV.

    Parameters
    ----------
    days:
        Number of historical days to replay.
    min_rr_override:
        Override ``take_profit_rr_ratio`` from risk profile.
    min_confidence_override:
        Override ``min_setup_confidence`` from risk profile.
    max_signals_override:
        Override ``max_daily_signals`` from risk profile.
    disable_volume_gate:
        If True, skip the volume confirmation gate entirely.
    disable_rs_gate:
        If True, skip the BTC relative strength gate entirely.
    disable_capitulation:
        If True, skip the capitulation bypass (F&G + RSI divergence).

    Returns
    -------
    list[DayResult]
        One result per simulated day.
    """
    risk_profile = load_risk_profile()
    sl_mult = risk_profile.get("stop_loss_atr_multiplier", 1.2)
    min_rr = min_rr_override or risk_profile.get("take_profit_rr_ratio", 2.0)
    min_confidence = min_confidence_override or risk_profile.get("min_setup_confidence", 0.65)
    max_signals = max_signals_override or risk_profile.get("max_daily_signals", 2)
    min_volume_ratio = 1.3

    # Load universe
    from tenth_floor.universe import load_universe

    universe = load_universe()
    pairs = universe.symbols(asset_class="crypto")  # backtest currently crypto-only

    # Fetch cached OHLCV — no API calls, just reads from Parquet cache
    logger.info("Loading cached OHLCV for %d pairs...", len(pairs))
    fetcher = MarketDataFetcher()
    all_data: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        try:
            df = fetcher.fetch_ohlcv(pair, "1d")
            if not df.empty:
                all_data[pair] = df
        except Exception:
            logger.warning("Failed to load data for %s — skipping", pair)

    if not all_data:
        logger.error("No OHLCV data available")
        return []

    # Determine date range from the shortest dataset
    min_len = min(len(df) for df in all_data.values())
    actual_days = min(days, min_len - 200)  # need 200 bars for EMA 200
    if actual_days <= 0:
        logger.error("Not enough historical data for %d-day replay (need 200+ bar runway)", days)
        return []

    # Fetch historical F&G for capitulation bypass
    fg_history: dict[str, int] = {}
    if not disable_capitulation:
        fg_history = _fetch_fg_history(actual_days)
        if not fg_history:
            logger.warning("No F&G history — capitulation bypass disabled for this run")

    logger.info(
        "Replaying %d days across %d pairs (min_rr=%.1f, min_conf=%.2f, max_signals=%d, capitulation=%s)",
        actual_days, len(all_data), min_rr, min_confidence, max_signals,
        "ON" if fg_history else "OFF",
    )

    builder = SnapshotBuilder()
    results: list[DayResult] = []

    for day_offset in range(actual_days, 0, -1):
        # Slice each pair's DataFrame to simulate "as of" that day
        snapshots = []
        for pair, full_df in all_data.items():
            end_idx = len(full_df) - day_offset
            if end_idx < 200:
                continue
            df_slice = full_df.iloc[:end_idx + 1].copy()

            try:
                snap = builder.build(df_slice, pair, "1d")
                snapshots.append(snap)
            except Exception:
                continue

        if not snapshots:
            continue

        # Determine the date from the first snapshot's bar timestamp
        first_snap = snapshots[0]
        sim_date = datetime.fromtimestamp(
            first_snap.bar_timestamp / 1000, tz=UTC
        ).strftime("%Y-%m-%d")

        day = DayResult(date=sim_date, pairs_analyzed=len(snapshots))

        # Check capitulation conditions for this day (F&G rising from extreme fear)
        fg_rising = False
        fg_val: int | None = None
        if fg_history:
            fg_rising, fg_val = _check_fg_rising_from_extreme(fg_history, sim_date)
        day.fg_value = fg_val
        day.fg_rising_from_extreme = fg_rising

        # Compute BTC baseline for relative strength gate
        btc_pct_change: float | None = None
        for snap in snapshots:
            if snap.symbol == "BTCUSDT" and len(snap.recent_closes) >= 2:
                newest, oldest = snap.recent_closes[0], snap.recent_closes[-1]
                if oldest > 0:
                    btc_pct_change = (newest - oldest) / oldest
                break

        candidates: list[tuple[float, float]] = []  # (trend_score, rr_ratio)
        btc_is_candidate = False

        for snap in snapshots:
            score = snap.indicators.trend_score
            if score is None:
                continue

            # Gate 1: Trend regime — strong downtrend → kill
            # EXCEPTION: capitulation bypass when F&G rising from extreme
            # fear AND RSI divergence detected on this pair
            if score <= _STRONG_DOWNTREND_MAX:
                has_rsi_div = getattr(snap.indicators, "rsi_divergence", None) is True
                if fg_rising and has_rsi_div:
                    day.capitulation_bypassed += 1
                else:
                    day.killed_trend_gate += 1
                    continue

            # Gate 2: Strategy simulation — low trend_score ≈ LLM would SKIP
            # If score < min_confidence, the pair would likely get SKIP from StrategyAgent
            if score < min_confidence:
                day.killed_strategy_skip += 1
                continue

            # Compute price levels for R:R check
            levels = _compute_price_levels(snap, sl_mult=sl_mult, min_rr=min_rr)
            rr = levels["reward_risk_ratio"]

            # Gate 3: Volume confirmation in downtrend
            if not disable_volume_gate and score <= _DOWNTREND_MAX:
                vol_ratio = snap.indicators.volume_ratio
                if vol_ratio is not None and vol_ratio < min_volume_ratio:
                    day.killed_volume_gate += 1
                    continue

            # Gate 4: BTC relative strength
            if (
                not disable_rs_gate
                and snap.symbol != "BTCUSDT"
                and btc_pct_change is not None
                and len(snap.recent_closes) >= 2
            ):
                alt_oldest = snap.recent_closes[-1]
                if alt_oldest > 0:
                    alt_pct = (snap.recent_closes[0] - alt_oldest) / alt_oldest
                    if alt_pct < btc_pct_change and btc_pct_change < 0:
                        day.killed_rs_gate += 1
                        continue

            # Gate 5: Confidence threshold
            if score < min_confidence:
                day.killed_confidence_gate += 1
                continue

            # Gate 6: R:R minimum
            if rr < min_rr:
                day.killed_rr_gate += 1
                continue

            day.proposals += 1
            candidates.append((score, rr))

            if snap.symbol == "BTCUSDT":
                btc_is_candidate = True

        # Gate 7: BTC correlation guard
        pre_corr = len(candidates)
        if not btc_is_candidate and len(candidates) > 2:
            candidates.sort(key=lambda x: x[0], reverse=True)
            candidates = candidates[:2]
        day.killed_btc_corr_gate = pre_corr - len(candidates)

        # Gate 8: Signal cap
        pre_cap = len(candidates)
        if len(candidates) > max_signals:
            candidates.sort(key=lambda x: x[0], reverse=True)
            candidates = candidates[:max_signals]
        day.killed_signal_cap = pre_cap - len(candidates)

        day.approved = len(candidates)
        results.append(day)

    return results


def print_summary(results: list[DayResult]) -> None:
    """Print a human-readable summary of the backtest results."""
    if not results:
        print("No results.")
        return

    total_days = len(results)
    total_approved = sum(r.approved for r in results)
    days_with_signals = sum(1 for r in results if r.approved > 0)

    # Aggregate gate kills
    total_trend = sum(r.killed_trend_gate for r in results)
    total_bypass = sum(r.capitulation_bypassed for r in results)
    total_strategy = sum(r.killed_strategy_skip for r in results)
    total_volume = sum(r.killed_volume_gate for r in results)
    total_rs = sum(r.killed_rs_gate for r in results)
    total_conf = sum(r.killed_confidence_gate for r in results)
    total_rr = sum(r.killed_rr_gate for r in results)
    total_corr = sum(r.killed_btc_corr_gate for r in results)
    total_cap = sum(r.killed_signal_cap for r in results)
    total_pairs = sum(r.pairs_analyzed for r in results)

    # F&G stats
    days_extreme_fear = sum(
        1 for r in results if isinstance(r.fg_value, int) and r.fg_value < 25
    )
    days_fg_rising = sum(1 for r in results if r.fg_rising_from_extreme)

    print(f"\n{'='*60}")
    print(f"  BACKTEST SUMMARY — {total_days} days")
    print(f"  {results[0].date}  →  {results[-1].date}")
    print(f"{'='*60}")
    print(f"  Total pair-days analysed:    {total_pairs}")
    print(f"  Signals approved:            {total_approved}")
    print(f"  Days with ≥1 signal:         {days_with_signals} / {total_days} ({days_with_signals/total_days*100:.0f}%)")
    print(f"  Avg signals/day:             {total_approved/total_days:.1f}")
    print()
    print("  Gate Kill Breakdown:")
    print(f"    Trend regime (strong ↓):   {total_trend:5d}")
    if total_bypass > 0:
        print(f"    Capitulation bypass:       {total_bypass:5d}  ← saved from trend gate")
    print(f"    Strategy (low score):      {total_strategy:5d}")
    print(f"    Volume gate:               {total_volume:5d}")
    print(f"    BTC relative strength:     {total_rs:5d}")
    print(f"    Confidence threshold:      {total_conf:5d}")
    print(f"    R:R below minimum:         {total_rr:5d}")
    print(f"    BTC correlation guard:     {total_corr:5d}")
    print(f"    Signal cap:                {total_cap:5d}")
    print()
    print("  Fear & Greed Context:")
    print(f"    Days in extreme fear:      {days_extreme_fear:5d} / {total_days}")
    print(f"    Days F&G rising from low:  {days_fg_rising:5d}")
    print(f"    Pair-days bypassed:        {total_bypass:5d}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest pipeline gates against cached OHLCV data (no LLM)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of historical days to replay (default: 90)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=["validation", "production"],
        help="Risk profile overlay",
    )
    parser.add_argument(
        "--min-rr",
        type=float,
        default=None,
        help="Override minimum R:R ratio (default: from risk profile)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Override minimum confidence threshold (default: from risk profile)",
    )
    parser.add_argument(
        "--max-signals",
        type=int,
        default=None,
        help="Override max daily signals (default: from risk profile)",
    )
    parser.add_argument(
        "--no-volume-gate",
        action="store_true",
        help="Disable the volume confirmation gate",
    )
    parser.add_argument(
        "--no-rs-gate",
        action="store_true",
        help="Disable the BTC relative strength gate",
    )
    parser.add_argument(
        "--no-capitulation",
        action="store_true",
        help="Disable the capitulation bypass (F&G + RSI divergence)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.profile:
        set_active_profile(args.profile)

    results = replay(
        days=args.days,
        min_rr_override=args.min_rr,
        min_confidence_override=args.min_confidence,
        max_signals_override=args.max_signals,
        disable_volume_gate=args.no_volume_gate,
        disable_rs_gate=args.no_rs_gate,
        disable_capitulation=args.no_capitulation,
    )

    print_summary(results)


if __name__ == "__main__":
    main()
