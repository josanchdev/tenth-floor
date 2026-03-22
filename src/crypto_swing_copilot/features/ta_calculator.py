"""
Compute technical analysis indicators from OHLCV data using pandas-ta.

All arithmetic lives here — this is the **only** module that touches raw
candle data and produces the ``TAIndicators`` Pydantic model consumed by
every downstream agent.

Design rules (from docs/architecture.md §2):
  • Pure data transformation — no LLM calls, no agent imports.
  • NaN → None — insufficient-data indicators become ``None`` in the model.
  • No side effects — the input DataFrame is never modified.

Usage as CLI test mode::

    python -m crypto_swing_copilot.features.ta_calculator
    python -m crypto_swing_copilot.features.ta_calculator BTCUSDT  # if cached

Usage as library::

    from crypto_swing_copilot.features.ta_calculator import TACalculator

    calc = TACalculator()
    indicators = calc.compute(ohlcv_df)
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

# pandas-ta uses numba @njit for SMA which is incompatible with
# pandas 3.x + Python 3.13.  Disable JIT to use the pure-Python fallback.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

# pandas-ta 0.4.71b + pandas 3.x: ``from numpy import isnan`` in
# ``true_range.py`` chokes on pandas Series objects.  We must patch
# numpy.isnan *before* pandas_ta imports it, so the ``from numpy import
# isnan`` statement picks up the safe wrapper.
import numpy as np

_orig_np_isnan = np.isnan


def _safe_isnan(x):  # type: ignore[no-untyped-def]
    if hasattr(x, "to_numpy"):
        return _orig_np_isnan(x.to_numpy(dtype=float, na_value=np.nan))
    arr = np.asarray(x)
    if arr.dtype.kind not in ("f", "c"):  # not float/complex
        arr = arr.astype(float)
    return _orig_np_isnan(arr)


np.isnan = _safe_isnan

import pandas as pd
import pandas_ta as ta  # noqa: F401, E402 — attaches .ta accessor to DataFrame

# Restore original numpy.isnan so other code is unaffected.
np.isnan = _orig_np_isnan

from crypto_swing_copilot.data.models import TAIndicators

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None for NaN/None/inf."""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _last_value(series: pd.Series | None) -> float | None:
    """Extract the last non-NaN-safe value from a pandas Series."""
    if series is None or series.empty:
        return None
    return _safe_float(series.iloc[-1])


class TACalculator:
    """Compute all technical indicators defined in ``TAIndicators``.

    Takes an OHLCV DataFrame (columns: ``timestamp, open, high, low, close,
    volume``) and returns a ``TAIndicators`` Pydantic model with the latest
    values for each indicator.

    All computation uses ``pandas-ta``.  Every numeric output is validated
    through ``_safe_float`` to convert NaN → None.
    """

    # ------------------------------------------------------------------
    # Core compute
    # ------------------------------------------------------------------

    def compute(self, df: pd.DataFrame) -> TAIndicators:
        """Compute all TA indicators from an OHLCV DataFrame.

        Parameters
        ----------
        df:
            OHLCV DataFrame with columns ``open, high, low, close, volume``.
            Must be sorted by ``timestamp`` ascending.

        Returns
        -------
        TAIndicators
            Pydantic model with the latest value for each indicator.
            Fields are ``None`` when there is insufficient data.
        """
        if df.empty:
            logger.warning("Empty DataFrame — returning all-None indicators")
            return TAIndicators()

        # Ensure we work on a copy so we don't mutate the caller's data
        df = df.copy()

        # --- EMAs ---
        ema_20 = _last_value(df.ta.ema(length=20))
        ema_50 = _last_value(df.ta.ema(length=50))
        ema_200 = _last_value(df.ta.ema(length=200))

        # --- RSI ---
        rsi_14 = _last_value(df.ta.rsi(length=14))

        # --- MACD (12, 26, 9) ---
        # pandas-ta returns: [MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9]
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        macd_line = None
        macd_signal = None
        macd_histogram = None
        if macd_df is not None and not macd_df.empty:
            macd_line = _last_value(macd_df.iloc[:, 0])      # MACD line
            macd_histogram = _last_value(macd_df.iloc[:, 1])  # Histogram (MACDh)
            macd_signal = _last_value(macd_df.iloc[:, 2])     # Signal (MACDs)

        # --- Bollinger Bands (20, 2) ---
        # pandas-ta returns: [BBL, BBM, BBU, BBB, BBP]
        bb_df = df.ta.bbands(length=20, std=2)
        bb_lower = None
        bb_middle = None
        bb_upper = None
        if bb_df is not None and not bb_df.empty:
            bb_lower = _last_value(bb_df.iloc[:, 0])    # Lower band (BBL)
            bb_middle = _last_value(bb_df.iloc[:, 1])    # Middle / SMA (BBM)
            bb_upper = _last_value(bb_df.iloc[:, 2])     # Upper band (BBU)

        # --- ATR ---
        atr_14 = _last_value(df.ta.atr(length=14))

        # --- OBV ---
        obv = _last_value(df.ta.obv())

        # --- Volume SMA 20 ---
        volume_sma_20 = _last_value(df["volume"].rolling(20).mean())

        # --- Volume ratio (latest bar volume / SMA 20) ---
        # >1.5 = notable activity, >2.0 = volume surge
        latest_volume = _safe_float(df["volume"].iloc[-1]) if not df.empty else None
        volume_ratio: float | None = None
        if latest_volume is not None and volume_sma_20 is not None and volume_sma_20 > 0:
            volume_ratio = round(latest_volume / volume_sma_20, 2)

        # --- Swing highs / lows (structural S/R) ---
        support_levels, resistance_levels = self._detect_swing_levels(df)

        indicators = TAIndicators(
            ema_20=ema_20,
            ema_50=ema_50,
            ema_200=ema_200,
            rsi_14=rsi_14,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            atr_14=atr_14,
            obv=obv,
            volume_sma_20=volume_sma_20,
            volume_ratio=volume_ratio,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )

        logger.info(
            "TAIndicators computed  ema20=%s  rsi=%s  atr=%s  obv=%s",
            indicators.ema_20,
            indicators.rsi_14,
            indicators.atr_14,
            indicators.obv,
        )
        return indicators

    # ------------------------------------------------------------------
    # Structural support / resistance (swing pivots)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_swing_levels(
        df: pd.DataFrame,
        order: int = 5,
        max_levels: int = 5,
    ) -> tuple[list[float], list[float]]:
        """Detect recent swing lows (support) and swing highs (resistance).

        A swing low at bar *i* means ``low[i]`` is the minimum of the
        window ``low[i-order : i+order+1]``.  Similarly for swing highs
        using the ``high`` column.

        Parameters
        ----------
        df:
            OHLCV DataFrame sorted by timestamp ascending.
        order:
            Number of bars on each side required to confirm a pivot.
            Default 5 works well for 4h candles (~20 h look-around).
        max_levels:
            Cap on returned levels (most recent kept).

        Returns
        -------
        tuple[list[float], list[float]]
            ``(support_levels, resistance_levels)`` — each sorted ascending
            by price.  Empty lists when data is insufficient.
        """
        if df.empty or len(df) < 2 * order + 1:
            return [], []

        lows = df["low"].values
        highs = df["high"].values
        n = len(df)

        supports: list[float] = []
        resistances: list[float] = []

        for i in range(order, n - order):
            window_low = lows[i - order: i + order + 1]
            if lows[i] == window_low.min():
                supports.append(round(float(lows[i]), 2))

            window_high = highs[i - order: i + order + 1]
            if highs[i] == window_high.max():
                resistances.append(round(float(highs[i]), 2))

        # Keep most recent, then sort by price ascending
        supports = sorted(set(supports[-max_levels:]))
        resistances = sorted(set(resistances[-max_levels:]))

        return supports, resistances

    # ------------------------------------------------------------------
    # Compute with historical context (for PairSnapshot)
    # ------------------------------------------------------------------

    def compute_with_history(
        self,
        df: pd.DataFrame,
        n_recent: int = 20,
    ) -> tuple[TAIndicators, list[float], list[float]]:
        """Compute indicators plus recent closes and volumes for PairSnapshot.

        Parameters
        ----------
        df:
            OHLCV DataFrame sorted by timestamp ascending.
        n_recent:
            Number of recent bars to include (default 20, newest first).

        Returns
        -------
        tuple
            ``(indicators, recent_closes, recent_volumes)``
            - ``recent_closes``: last N close prices, newest first.
            - ``recent_volumes``: last N volumes, newest first.
        """
        indicators = self.compute(df)

        # Recent closes and volumes (newest first)
        recent_closes: list[float] = []
        recent_volumes: list[float] = []

        if not df.empty:
            tail = df.tail(n_recent)
            recent_closes = [
                v for v in tail["close"].iloc[::-1].tolist()
                if _safe_float(v) is not None
            ]
            recent_volumes = [
                v for v in tail["volume"].iloc[::-1].tolist()
                if _safe_float(v) is not None
            ]

        return indicators, recent_closes, recent_volumes


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Quick test — compute indicators from cached data or synthetic bars.

    Usage::

        python -m crypto_swing_copilot.features.ta_calculator           # synthetic
        python -m crypto_swing_copilot.features.ta_calculator BTCUSDT   # cached
    """
    import sys

    import numpy as np

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    console.print("\n[bold cyan]crypto-swing-copilot · TA Calculator[/bold cyan]\n")

    args = sys.argv[1:]

    if args:
        # Try to load cached data
        symbol = args[0]
        try:
            from crypto_swing_copilot.data.market_data import MarketDataFetcher

            fetcher = MarketDataFetcher()
            df = fetcher.fetch_ohlcv(symbol, "4h")
            source = f"cached {symbol} 4h"
        except Exception as exc:
            console.print(f"[yellow]Could not load cached data for {symbol}: {exc}[/yellow]")
            console.print("[dim]Falling back to synthetic data...[/dim]\n")
            df = _generate_synthetic_ohlcv(250)
            source = "synthetic (250 bars)"
    else:
        df = _generate_synthetic_ohlcv(250)
        source = "synthetic (250 bars)"

    console.print(f"Data source: [bold]{source}[/bold]  ({len(df)} bars)\n")

    calc = TACalculator()
    indicators, recent_closes, recent_volumes = calc.compute_with_history(df)

    # --- Indicators table ---
    table = Table(title="Technical Indicators (latest bar)", show_lines=True)
    table.add_column("Indicator", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right", style="white")

    for field_name, value in indicators.model_dump().items():
        display = f"{value:.6f}" if isinstance(value, float) else str(value)
        style = "dim" if value is None else ""
        table.add_row(field_name, display, style=style)

    console.print(table)

    # --- Recent context ---
    console.print(
        Panel(
            f"Closes (newest→oldest): {[round(c, 2) for c in recent_closes[:5]]} ...\n"
            f"Volumes (newest→oldest): {[round(v, 2) for v in recent_volumes[:5]]} ...",
            title=f"Recent Context ({len(recent_closes)} bars)",
            border_style="cyan",
        )
    )

    console.print("[bold green]✓ Done.[/bold green]\n")


def _generate_synthetic_ohlcv(n: int = 250) -> pd.DataFrame:
    """Generate N synthetic OHLCV bars for testing."""
    import numpy as np

    np.random.seed(42)
    base_price = 60000.0
    timestamps = list(range(0, n * 14_400_000, 14_400_000))  # 4h bars

    closes = [base_price]
    for _ in range(n - 1):
        change = np.random.normal(0, 0.005) * closes[-1]
        closes.append(max(closes[-1] + change, 100))

    data = {
        "timestamp": timestamps[:n],
        "open": [c * (1 + np.random.uniform(-0.002, 0.002)) for c in closes],
        "high": [c * (1 + abs(np.random.normal(0, 0.003))) for c in closes],
        "low": [c * (1 - abs(np.random.normal(0, 0.003))) for c in closes],
        "close": closes,
        "volume": [abs(np.random.normal(500, 100)) for _ in range(n)],
    }
    return pd.DataFrame(data)


if __name__ == "__main__":
    _cli_main()
