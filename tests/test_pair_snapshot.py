"""Unit tests for SnapshotBuilder — synthetic data, no network calls."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_swing_copilot.data.models import (
    PairSnapshot,
    RSSHeadline,
    SentimentSnapshot,
    TAIndicators,
)
from crypto_swing_copilot.features.pair_snapshot import SnapshotBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 250, seed: int = 42) -> pd.DataFrame:
    """Generate N synthetic OHLCV bars."""
    rng = np.random.default_rng(seed)
    base = 60000.0
    closes = [base]
    for _ in range(n - 1):
        closes.append(max(closes[-1] + rng.normal(0, 0.005) * closes[-1], 100))

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
def builder() -> SnapshotBuilder:
    return SnapshotBuilder()


@pytest.fixture
def df_250() -> pd.DataFrame:
    return _make_ohlcv(250)


@pytest.fixture
def sentiment() -> SentimentSnapshot:
    return SentimentSnapshot(
        fear_greed_value=65,
        fear_greed_label="Greed",
        fear_greed_trend=[65, 60, 55],
        headlines=[
            RSSHeadline(title="BTC rallies", source="coindesk"),
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildSingle:
    """Tests for build() — single pair."""

    def test_returns_pair_snapshot(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        assert isinstance(snap, PairSnapshot)

    def test_symbol_and_timeframe(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        assert snap.symbol == "BTCUSDT"
        assert snap.timeframe == "4h"

    def test_current_price_from_last_close(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        expected = float(df_250["close"].iloc[-1])
        assert snap.current_price == pytest.approx(expected)

    def test_bar_timestamp_from_last_row(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        expected = int(df_250["timestamp"].iloc[-1])
        assert snap.bar_timestamp == expected

    def test_indicators_populated(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        assert isinstance(snap.indicators, TAIndicators)
        # With 250 bars, all indicators should be non-None
        assert snap.indicators.ema_20 is not None
        assert snap.indicators.rsi_14 is not None

    def test_recent_closes_length(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        assert len(snap.recent_closes) == 20
        assert len(snap.recent_volumes) == 20

    def test_sentiment_none_by_default(
        self, builder: SnapshotBuilder, df_250: pd.DataFrame
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h")
        assert snap.sentiment is None


class TestBuildWithSentiment:
    """Tests for build() with sentiment attached."""

    def test_sentiment_passes_through(
        self,
        builder: SnapshotBuilder,
        df_250: pd.DataFrame,
        sentiment: SentimentSnapshot,
    ) -> None:
        snap = builder.build(df_250, "BTCUSDT", "4h", sentiment=sentiment)
        assert snap.sentiment is not None
        assert snap.sentiment.fear_greed_value == 65
        assert snap.sentiment.fear_greed_label == "Greed"
        assert len(snap.sentiment.headlines) == 1


class TestBuildUniverse:
    """Tests for build_universe() — multiple pairs."""

    def test_builds_multiple_snapshots(self, builder: SnapshotBuilder) -> None:
        data = {
            "BTCUSDT": {"4h": _make_ohlcv(250, seed=1)},
            "ETHUSDT": {"4h": _make_ohlcv(250, seed=2)},
        }
        snapshots = builder.build_universe(data)
        assert len(snapshots) == 2
        symbols = {s.symbol for s in snapshots}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    def test_skips_empty_dataframes(self, builder: SnapshotBuilder) -> None:
        data = {
            "BTCUSDT": {"4h": _make_ohlcv(250)},
            "ETHUSDT": {"4h": pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])},
        }
        snapshots = builder.build_universe(data)
        assert len(snapshots) == 1
        assert snapshots[0].symbol == "BTCUSDT"


class TestEdgeCases:
    """Edge cases."""

    def test_empty_df_raises(self, builder: SnapshotBuilder) -> None:
        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        with pytest.raises(ValueError, match="empty DataFrame"):
            builder.build(df, "BTCUSDT", "4h")
