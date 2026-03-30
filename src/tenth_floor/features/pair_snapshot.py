"""
Assemble ``PairSnapshot`` models from OHLCV data, TA indicators, and sentiment.

``PairSnapshot`` is the **single typed payload** forwarded to every agent.
This module is the bridge between the Data Layer / Feature Engine and the
Agent layer.

Design rules (from docs/architecture.md §2):
  • Pure data transformation — no LLM calls, no agent imports.
  • Calls ``TACalculator`` internally; never re-implements indicator math.

Usage as library::

    from tenth_floor.features.pair_snapshot import SnapshotBuilder

    builder = SnapshotBuilder()
    snapshot = builder.build(ohlcv_df, "BTCUSDT", "1d", sentiment=sentiment)
"""

from __future__ import annotations

import logging

import pandas as pd

from tenth_floor.data.models import PairSnapshot, SentimentSnapshot
from tenth_floor.features.ta_calculator import TACalculator

logger = logging.getLogger(__name__)


class SnapshotBuilder:
    """Build ``PairSnapshot`` models from raw OHLCV DataFrames.

    Internally uses ``TACalculator`` to compute technical indicators
    and extracts latest price / timestamp from the DataFrame.
    """

    def __init__(self) -> None:
        self._calculator = TACalculator()

    def build(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        *,
        sentiment: SentimentSnapshot | None = None,
        n_recent: int = 20,
    ) -> PairSnapshot:
        """Build a single ``PairSnapshot`` from an OHLCV DataFrame.

        Parameters
        ----------
        df:
            OHLCV DataFrame (columns: timestamp, open, high, low, close, volume).
            Must be sorted by timestamp ascending and non-empty.
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        timeframe:
            Candle interval, e.g. ``"4h"`` or ``"1d"``.
        sentiment:
            Optional shared ``SentimentSnapshot`` (same for all pairs in a run).
        n_recent:
            Number of recent bars to include for historical context.

        Returns
        -------
        PairSnapshot
            Fully assembled snapshot ready for agent consumption.

        Raises
        ------
        ValueError
            If the DataFrame is empty.
        """
        if df.empty:
            raise ValueError(f"Cannot build PairSnapshot for {symbol}/{timeframe}: empty DataFrame")

        # Extract latest bar info
        last_row = df.iloc[-1]
        current_price = float(last_row["close"])
        bar_timestamp = int(last_row["timestamp"])

        # Compute TA indicators + recent history
        indicators, recent_closes, recent_volumes = self._calculator.compute_with_history(
            df, n_recent=n_recent
        )

        snapshot = PairSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            bar_timestamp=bar_timestamp,
            indicators=indicators,
            sentiment=sentiment,
            recent_closes=recent_closes,
            recent_volumes=recent_volumes,
        )

        logger.info(
            "PairSnapshot built  %s %s  price=%.2f  indicators=%d non-None  headlines=%d",
            symbol,
            timeframe,
            current_price,
            sum(1 for v in indicators.model_dump().values() if v is not None),
            len(sentiment.headlines) if sentiment else 0,
        )
        return snapshot

    def build_universe(
        self,
        data: dict[str, dict[str, pd.DataFrame]],
        *,
        sentiment: SentimentSnapshot | None = None,
        n_recent: int = 20,
    ) -> list[PairSnapshot]:
        """Build snapshots for all pairs × timeframes.

        Parameters
        ----------
        data:
            Output of ``MarketDataFetcher.fetch_universe()`` —
            ``{symbol: {timeframe: DataFrame}}``.
        sentiment:
            Shared sentiment snapshot for the entire run.
        n_recent:
            Number of recent bars per snapshot.

        Returns
        -------
        list[PairSnapshot]
            One snapshot per pair × timeframe combination.
            Empty DataFrames are skipped with a warning.
        """
        snapshots: list[PairSnapshot] = []

        for symbol, tf_data in data.items():
            for timeframe, df in tf_data.items():
                if df.empty:
                    logger.warning("Skipping %s %s — empty DataFrame", symbol, timeframe)
                    continue

                try:
                    snap = self.build(
                        df, symbol, timeframe,
                        sentiment=sentiment,
                        n_recent=n_recent,
                    )
                    snapshots.append(snap)
                except Exception:
                    logger.exception("Failed to build snapshot for %s %s", symbol, timeframe)

        logger.info("Built %d snapshots from %d pairs", len(snapshots), len(data))
        return snapshots
