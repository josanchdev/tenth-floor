"""Unit tests for TACalculator — uses synthetic OHLCV data, no network calls."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_swing_copilot.data.models import TAIndicators
from crypto_swing_copilot.features.ta_calculator import TACalculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 250, seed: int = 42) -> pd.DataFrame:
    """Generate N synthetic OHLCV bars with realistic price action."""
    rng = np.random.default_rng(seed)
    base = 60000.0
    closes = [base]
    for _ in range(n - 1):
        change = rng.normal(0, 0.005) * closes[-1]
        closes.append(max(closes[-1] + change, 100))

    return pd.DataFrame(
        {
            "timestamp": list(range(0, n * 14_400_000, 14_400_000)),
            "open": [c * (1 + rng.uniform(-0.002, 0.002)) for c in closes],
            "high": [c * (1 + abs(rng.normal(0, 0.003))) for c in closes],
            "low": [c * (1 - abs(rng.normal(0, 0.003))) for c in closes],
            "close": closes,
            "volume": [abs(rng.normal(500, 100)) for _ in range(n)],
        }
    )


@pytest.fixture
def calculator() -> TACalculator:
    return TACalculator()


@pytest.fixture
def df_250() -> pd.DataFrame:
    """250 synthetic bars — enough for all indicators including EMA 200."""
    return _make_ohlcv(250)


@pytest.fixture
def df_5() -> pd.DataFrame:
    """5 bars — insufficient for most long-period indicators."""
    return _make_ohlcv(5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeWithSufficientData:
    """250 bars — all indicators should be computable."""

    def test_returns_ta_indicators(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        result = calculator.compute(df_250)
        assert isinstance(result, TAIndicators)

    def test_all_fields_populated(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """With 250 bars, every indicator should be non-None."""
        result = calculator.compute(df_250)
        for field_name, value in result.model_dump().items():
            assert value is not None, f"{field_name} should not be None with 250 bars"

    def test_ema_ordering(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """For a relatively stable price, EMAs should be in reasonable range."""
        result = calculator.compute(df_250)
        assert result.ema_20 is not None
        assert result.ema_50 is not None
        assert result.ema_200 is not None
        # All EMAs should be positive (price > 0)
        assert result.ema_20 > 0
        assert result.ema_50 > 0
        assert result.ema_200 > 0

    def test_rsi_range(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """RSI must be in [0, 100]."""
        result = calculator.compute(df_250)
        assert result.rsi_14 is not None
        assert 0 <= result.rsi_14 <= 100

    def test_bollinger_band_ordering(
        self, calculator: TACalculator, df_250: pd.DataFrame
    ) -> None:
        """BB lower < BB middle < BB upper."""
        result = calculator.compute(df_250)
        assert result.bb_lower is not None
        assert result.bb_middle is not None
        assert result.bb_upper is not None
        assert result.bb_lower < result.bb_middle < result.bb_upper

    def test_atr_positive(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """ATR should always be non-negative."""
        result = calculator.compute(df_250)
        assert result.atr_14 is not None
        assert result.atr_14 >= 0

    def test_macd_fields_populated(
        self, calculator: TACalculator, df_250: pd.DataFrame
    ) -> None:
        result = calculator.compute(df_250)
        assert result.macd_line is not None
        assert result.macd_signal is not None
        assert result.macd_histogram is not None


class TestComputeWithInsufficientData:
    """5 bars — some indicators should gracefully be None."""

    def test_ema_200_is_none(self, calculator: TACalculator, df_5: pd.DataFrame) -> None:
        """EMA 200 needs 200 bars — should be None with only 5."""
        result = calculator.compute(df_5)
        assert result.ema_200 is None

    def test_no_crash(self, calculator: TACalculator, df_5: pd.DataFrame) -> None:
        """Should not raise even with very few bars."""
        result = calculator.compute(df_5)
        assert isinstance(result, TAIndicators)


class TestComputeEdgeCases:
    """Edge cases and NaN handling."""

    def test_empty_dataframe(self, calculator: TACalculator) -> None:
        """Empty DataFrame → all-None indicators."""
        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        result = calculator.compute(df)
        assert isinstance(result, TAIndicators)
        for value in result.model_dump().values():
            assert value is None

    def test_does_not_mutate_input(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """compute() should not modify the input DataFrame."""
        original_shape = df_250.shape
        original_cols = list(df_250.columns)
        calculator.compute(df_250)
        assert df_250.shape == original_shape
        assert list(df_250.columns) == original_cols


class TestComputeWithHistory:
    """Tests for compute_with_history()."""

    def test_returns_triple(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        result = calculator.compute_with_history(df_250)
        assert len(result) == 3
        indicators, closes, volumes = result
        assert isinstance(indicators, TAIndicators)
        assert isinstance(closes, list)
        assert isinstance(volumes, list)

    def test_recent_closes_length(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        _, closes, volumes = calculator.compute_with_history(df_250, n_recent=20)
        assert len(closes) == 20
        assert len(volumes) == 20

    def test_newest_first(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        """Recent closes should be newest-first."""
        _, closes, _ = calculator.compute_with_history(df_250, n_recent=5)
        # The last close in the df should be the first in recent_closes
        last_close = df_250["close"].iloc[-1]
        assert closes[0] == pytest.approx(last_close)

    def test_custom_n_recent(self, calculator: TACalculator, df_250: pd.DataFrame) -> None:
        _, closes, _ = calculator.compute_with_history(df_250, n_recent=10)
        assert len(closes) == 10
