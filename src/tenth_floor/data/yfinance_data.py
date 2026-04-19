"""
Fetch OHLCV candle data from Yahoo Finance via yfinance.

Covers US equities, ETFs, and commodity ETFs. Data is cached locally
as Parquet files under ``data/raw/`` using the same layout as the
ccxt-based MarketDataFetcher.

Usage as library::

    from tenth_floor.data.yfinance_data import YFinanceDataFetcher

    fetcher = YFinanceDataFetcher()
    df = fetcher.fetch_ohlcv("AAPL", "1d")
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
import yfinance as yf

from tenth_floor.config import CONFIG_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

_OHLCV_COLUMNS: Final[list[str]] = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

# yfinance interval strings for our timeframes
_YF_INTERVALS: Final[dict[str, str]] = {
    "1d": "1d",
    "1h": "1h",
    "4h": "1h",  # yfinance doesn't have 4h; we'll resample from 1h
}


def _load_services_config() -> dict:
    """Load ``config/services.yaml`` and return the ``market_data`` section."""
    path = CONFIG_DIR / "services.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("market_data", {})


class YFinanceDataFetcher:
    """Yahoo Finance OHLCV fetcher with local Parquet caching.

    Same interface and cache layout as ``MarketDataFetcher`` so the
    pipeline can use either interchangeably.

    Parameters
    ----------
    cache_dir:
        Override the Parquet cache directory.
    lookback_days:
        Number of calendar days to fetch on first download. Default 730
        (2 years — yfinance free tier limit for daily data).
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        lookback_days: int = 730,
    ) -> None:
        cfg = _load_services_config()

        raw_cache = cache_dir or cfg.get("cache_dir", "data/raw")
        self._cache_dir = (
            Path(raw_cache) if Path(raw_cache).is_absolute() else PROJECT_ROOT / raw_cache
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lookback_days = lookback_days

        logger.info(
            "YFinanceDataFetcher initialised  cache=%s  lookback=%dd",
            self._cache_dir,
            self._lookback_days,
        )

    # ------------------------------------------------------------------
    # Parquet cache helpers (same layout as MarketDataFetcher)
    # ------------------------------------------------------------------

    def _parquet_path(self, symbol: str, timeframe: str) -> Path:
        parent = self._cache_dir / symbol.upper()
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{timeframe}.parquet"

    def _read_cache(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        path = self._parquet_path(symbol, timeframe)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        logger.debug("Cache hit  %s %s  rows=%d", symbol, timeframe, len(df))
        return df

    def _write_cache(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        path = self._parquet_path(symbol, timeframe)
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, path, compression="snappy")
        logger.debug("Cache write  %s %s  rows=%d", symbol, timeframe, len(df))
        return path

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _fetch_raw(
        self,
        symbol: str,
        timeframe: str,
        start: str | None = None,
    ) -> pd.DataFrame:
        """Download OHLCV from Yahoo Finance.

        Parameters
        ----------
        symbol:
            Ticker symbol, e.g. ``"AAPL"`` or ``"GLD"``.
        timeframe:
            ``"1d"`` (only daily supported for now).
        start:
            ISO date string for start of fetch window. If None, uses
            lookback_days from today.
        """
        if start is None:
            start_date = datetime.now(UTC) - timedelta(days=self._lookback_days)
            start = start_date.strftime("%Y-%m-%d")

        yf_interval = _YF_INTERVALS.get(timeframe, "1d")

        logger.debug("Fetching  %s %s  start=%s  interval=%s", symbol, timeframe, start, yf_interval)

        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start, interval=yf_interval, auto_adjust=True)

        if hist.empty:
            logger.warning("No data returned for %s %s", symbol, timeframe)
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        # yfinance returns a DatetimeIndex with columns: Open, High, Low, Close, Volume
        df = pd.DataFrame(
            {
                "timestamp": (hist.index.astype("int64") // 10**6).astype("int64"),
                "open": hist["Open"].values,
                "high": hist["High"].values,
                "low": hist["Low"].values,
                "close": hist["Close"].values,
                "volume": hist["Volume"].values,
            }
        )

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = df["timestamp"].astype("int64")

        # Drop any rows with NaN prices (can happen on partial trading days)
        df = df.dropna(subset=["open", "high", "low", "close"])

        # Deduplicate and sort
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)

        return df

    def fetch_last_price(self, symbol: str) -> float:
        """Return the most recent quote price via yfinance fast_info.

        Used for re-snapping ``entry_price`` right before a signal is
        persisted, so the published entry reflects the price at publish
        time rather than at the start of the pipeline.
        """
        ticker = yf.Ticker(symbol)
        try:
            last = ticker.fast_info["last_price"]
        except (KeyError, AttributeError):
            last = ticker.info.get("regularMarketPrice")
        if last is None:
            raise ValueError(f"No last price for {symbol}")
        return float(last)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data with incremental caching.

        Same pattern as MarketDataFetcher: read cache, fetch new bars,
        merge, write back.

        Returns
        -------
        pd.DataFrame
            Columns: ``timestamp, open, high, low, close, volume``.
            Sorted by ``timestamp`` ascending. ``timestamp`` is Unix ms.
        """
        cached = self._read_cache(symbol, timeframe)

        start: str | None = None
        if cached is not None and not cached.empty:
            # Fetch from day after last cached bar
            last_ts = int(cached["timestamp"].iloc[-1])
            last_date = datetime.fromtimestamp(last_ts / 1000, tz=UTC)
            next_day = last_date + timedelta(days=1)
            if next_day.date() > datetime.now(UTC).date():
                # Cache already covers today — nothing to fetch
                logger.info("Cache up-to-date  %s %s", symbol, timeframe)
                return cached
            start = next_day.strftime("%Y-%m-%d")
            logger.info(
                "Incremental fetch  %s %s  since=%s",
                symbol, timeframe, start,
            )

        new_bars = self._fetch_raw(symbol, timeframe, start=start)

        if cached is not None and not cached.empty:
            if new_bars.empty:
                df = cached
            else:
                df = pd.concat([cached, new_bars], ignore_index=True)
                df = df.drop_duplicates(subset=["timestamp"], keep="last")
                df = df.sort_values("timestamp").reset_index(drop=True)
        else:
            df = new_bars

        if df.empty:
            logger.warning("No data for %s %s — nothing to cache", symbol, timeframe)
            return df

        self._write_cache(df, symbol, timeframe)

        logger.info(
            "✓ %s %s  rows=%d  range=%s → %s",
            symbol,
            timeframe,
            len(df),
            datetime.fromtimestamp(
                int(df["timestamp"].iloc[0]) / 1000, tz=UTC
            ).strftime("%Y-%m-%d"),
            datetime.fromtimestamp(
                int(df["timestamp"].iloc[-1]) / 1000, tz=UTC
            ).strftime("%Y-%m-%d %H:%M"),
        )

        return df

    def fetch_universe(
        self,
        symbols: list[str],
        timeframe: str = "1d",
    ) -> dict[str, dict[str, pd.DataFrame]]:
        """Fetch OHLCV for a list of symbols.

        Returns
        -------
        dict
            ``{symbol: {timeframe: DataFrame}}``.
        """
        results: dict[str, dict[str, pd.DataFrame]] = {}
        total = len(symbols)

        for i, symbol in enumerate(symbols, 1):
            logger.info("[%d/%d] Fetching %s %s …", i, total, symbol, timeframe)
            try:
                results[symbol] = {timeframe: self.fetch_ohlcv(symbol, timeframe)}
            except Exception:
                logger.exception("Failed to fetch %s %s", symbol, timeframe)
                results[symbol] = {timeframe: pd.DataFrame(columns=_OHLCV_COLUMNS)}

        return results

    def cache_info(self, symbol: str, timeframe: str) -> dict:
        """Return metadata about the cached data for a symbol."""
        path = self._parquet_path(symbol, timeframe)
        info: dict = {"path": str(path), "exists": path.exists()}
        if path.exists():
            df = pd.read_parquet(path)
            info["rows"] = len(df)
            if not df.empty:
                info["first_ts"] = datetime.fromtimestamp(
                    int(df["timestamp"].iloc[0]) / 1000, tz=UTC
                ).isoformat()
                info["last_ts"] = datetime.fromtimestamp(
                    int(df["timestamp"].iloc[-1]) / 1000, tz=UTC
                ).isoformat()
                info["size_kb"] = round(path.stat().st_size / 1024, 1)
        return info
