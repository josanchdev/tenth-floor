"""
Fetch OHLCV candle data from Binance via ccxt (read-only, spot).

All data is cached locally as Parquet files under ``data/raw/``.
Subsequent runs only fetch new bars beyond what is already cached.

Usage as CLI test mode::

    python -m tenth_floor.data.market_data          # full universe
    python -m tenth_floor.data.market_data BTCUSDT  # single pair

Usage as library::

    from tenth_floor.data.market_data import MarketDataFetcher

    fetcher = MarketDataFetcher()
    df = fetcher.fetch_ohlcv("BTCUSDT", "4h")
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import ccxt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from tenth_floor.config import CONFIG_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

# Binance OHLCV column order returned by ccxt
_OHLCV_COLUMNS: Final[list[str]] = [
    "timestamp",  # Unix ms
    "open",
    "high",
    "low",
    "close",
    "volume",
]

# Timeframe → milliseconds (used for incremental fetch math)
_TF_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


def _load_services_config() -> dict:
    """Load ``config/services.yaml`` and return the ``market_data`` section."""
    path = CONFIG_DIR / "services.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("market_data", {})


def _load_timeframes() -> list[str]:
    """Return configured timeframes from ``config/risk_profile.json``."""
    path = CONFIG_DIR / "risk_profile.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("timeframes", ["4h", "1d"])


# ---------------------------------------------------------------------------
# Core fetcher
# ---------------------------------------------------------------------------


class MarketDataFetcher:
    """Read-only Binance spot OHLCV fetcher with local Parquet caching.

    Parameters
    ----------
    cache_dir:
        Override the Parquet cache directory.  Defaults to the value in
        ``config/services.yaml`` → ``market_data.cache_dir``.
    ohlcv_limit:
        Max candles per single API request.  Defaults to config value (500).
    rate_limit_ms:
        Milliseconds to sleep between paginated requests.

    Notes
    -----
    - Uses **public** (unauthenticated) endpoints only.
    - No order placement, no write operations to the exchange.
    - The ``ccxt.binance`` instance is created with ``enableRateLimit=True``.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        ohlcv_limit: int | None = None,
        rate_limit_ms: int | None = None,
    ) -> None:
        cfg = _load_services_config()

        # Cache directory (relative paths resolved against project root)
        raw_cache = cache_dir or cfg.get("cache_dir", "data/raw")
        self._cache_dir = (
            Path(raw_cache) if Path(raw_cache).is_absolute() else PROJECT_ROOT / raw_cache
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._ohlcv_limit: int = ohlcv_limit or int(cfg.get("ohlcv_limit", 500))
        self._rate_limit_ms: int = rate_limit_ms or int(cfg.get("rate_limit_ms", 200))

        # Initialise ccxt — public endpoints only, no API keys
        self._exchange = ccxt.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        logger.info(
            "MarketDataFetcher initialised  cache=%s  limit=%d  rate_limit=%dms",
            self._cache_dir,
            self._ohlcv_limit,
            self._rate_limit_ms,
        )

    # ------------------------------------------------------------------
    # Parquet cache helpers
    # ------------------------------------------------------------------

    def _parquet_path(self, symbol: str, timeframe: str) -> Path:
        """Return the cache file path for a given pair + timeframe.

        Layout: ``<cache_dir>/<SYMBOL>/<timeframe>.parquet``
        e.g.    ``data/raw/BTCUSDT/4h.parquet``
        """
        safe_symbol = symbol.replace("/", "_")
        parent = self._cache_dir / safe_symbol
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{timeframe}.parquet"

    def _read_cache(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """Read cached Parquet file. Returns ``None`` if not present."""
        path = self._parquet_path(symbol, timeframe)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        logger.debug("Cache hit  %s %s  rows=%d", symbol, timeframe, len(df))
        return df

    def _write_cache(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        """Write (or overwrite) the Parquet cache for a pair + timeframe."""
        path = self._parquet_path(symbol, timeframe)
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, path, compression="snappy")
        logger.debug("Cache write  %s %s  rows=%d  path=%s", symbol, timeframe, len(df), path)
        return path

    # ------------------------------------------------------------------
    # Gap detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_gaps(df: pd.DataFrame, timeframe: str) -> list[tuple[int, int]]:
        """Return a list of ``(expected_ts, actual_ts)`` where bars are missing.

        Raises
        ------
        ValueError
            If gaps are detected — we fail fast per v1 constraints rather
            than silently imputing missing bars.
        """
        tf_ms = _TF_MS.get(timeframe)
        if tf_ms is None or df.empty:
            return []

        ts = df["timestamp"].values
        gaps: list[tuple[int, int]] = []
        for i in range(1, len(ts)):
            expected = int(ts[i - 1]) + tf_ms
            actual = int(ts[i])
            if actual != expected:
                gaps.append((expected, actual))

        if gaps:
            sample = gaps[:5]
            msg = (
                f"OHLCV gap detected: {len(gaps)} missing bar(s). "
                f"First gaps (expected → actual ms): {sample}"
            )
            logger.warning(msg)

        return gaps

    # ------------------------------------------------------------------
    # Fetch + incremental update
    # ------------------------------------------------------------------

    def _fetch_raw(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
    ) -> pd.DataFrame:
        """Paginated OHLCV fetch from Binance. Returns a DataFrame.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTC/USDT"`` or ``"BTCUSDT"`` (both accepted).
        timeframe:
            Candle interval, e.g. ``"4h"`` or ``"1d"``.
        since:
            Start timestamp in Unix **milliseconds**. If ``None``, ccxt
            uses the exchange default (most recent bars).
        """
        # Normalise symbol format: ccxt expects "BTC/USDT"
        ccxt_symbol = self._normalise_symbol(symbol)

        all_candles: list[list] = []
        current_since = since

        while True:
            logger.debug(
                "Fetching  %s %s  since=%s  limit=%d",
                ccxt_symbol,
                timeframe,
                current_since,
                self._ohlcv_limit,
            )
            candles = self._exchange.fetch_ohlcv(
                ccxt_symbol,
                timeframe=timeframe,
                since=current_since,
                limit=self._ohlcv_limit,
            )

            if not candles:
                break

            all_candles.extend(candles)

            # If we got fewer than the limit, we've reached the latest bar
            if len(candles) < self._ohlcv_limit:
                break

            # Move `since` to one interval after the last candle
            tf_ms = _TF_MS.get(timeframe, 0)
            last_ts = candles[-1][0]
            current_since = last_ts + tf_ms

            # Rate-limit pause
            time.sleep(self._rate_limit_ms / 1000.0)

        if not all_candles:
            logger.warning("No candles returned for %s %s", ccxt_symbol, timeframe)
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        df = pd.DataFrame(all_candles, columns=_OHLCV_COLUMNS)

        # Deduplicate (in case of overlap between pages)
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = df["timestamp"].astype("int64")

        return df

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        check_gaps: bool = True,
    ) -> pd.DataFrame:
        """Fetch OHLCV data with incremental caching.

        1. Reads existing Parquet cache.
        2. Fetches only new bars since the last cached timestamp.
        3. Merges, deduplicates, and writes back.
        4. Optionally checks for gaps (warns but does not raise in v1).

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        timeframe:
            Candle interval — must be one of ``"4h"`` or ``"1d"`` in v1.
        check_gaps:
            If ``True``, log warnings when missing bars are detected.

        Returns
        -------
        pd.DataFrame
            Columns: ``timestamp, open, high, low, close, volume``.
            Sorted by ``timestamp`` ascending.  ``timestamp`` is Unix ms.
        """
        cached = self._read_cache(symbol, timeframe)

        since: int | None = None
        if cached is not None and not cached.empty:
            # Fetch from last cached timestamp onward to fill gap + catch updates
            last_ts = int(cached["timestamp"].iloc[-1])
            tf_ms = _TF_MS.get(timeframe, 0)
            since = last_ts + tf_ms
            logger.info(
                "Incremental fetch  %s %s  since=%s (%s)",
                symbol,
                timeframe,
                since,
                datetime.fromtimestamp(since / 1000, tz=UTC).isoformat(),
            )

        new_bars = self._fetch_raw(symbol, timeframe, since=since)

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

        # Persist merged result
        self._write_cache(df, symbol, timeframe)

        # Gap detection (warn-only in v1)
        if check_gaps:
            self._detect_gaps(df, timeframe)

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
        pairs: list[str] | None = None,
        timeframes: list[str] | None = None,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        """Fetch OHLCV for all pairs × timeframes.

        Returns
        -------
        dict
            ``{symbol: {timeframe: DataFrame}}``.
        """
        if pairs is None:
            from tenth_floor.universe import load_universe

            pairs = load_universe().symbols(asset_class="crypto")
        timeframes = timeframes or _load_timeframes()

        results: dict[str, dict[str, pd.DataFrame]] = {}
        total = len(pairs) * len(timeframes)
        fetched = 0

        for symbol in pairs:
            results[symbol] = {}
            for tf in timeframes:
                fetched += 1
                logger.info("[%d/%d] Fetching %s %s …", fetched, total, symbol, tf)
                try:
                    results[symbol][tf] = self.fetch_ohlcv(symbol, tf)
                except Exception:
                    logger.exception("Failed to fetch %s %s", symbol, tf)
                    results[symbol][tf] = pd.DataFrame(columns=_OHLCV_COLUMNS)

        return results

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        """Convert ``BTCUSDT`` → ``BTC/USDT`` if needed (ccxt format)."""
        if "/" in symbol:
            return symbol
        # Handle common USDT pairs
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB"):
            if symbol.endswith(quote):
                base = symbol[: -len(quote)]
                return f"{base}/{quote}"
        # Fallback: assume last 4 chars are the quote
        return f"{symbol[:-4]}/{symbol[-4:]}"

    def cache_info(self, symbol: str, timeframe: str) -> dict:
        """Return metadata about the cached data for a pair + timeframe."""
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


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Quick test mode — run from the command line to validate fetching.

    Usage::

        python -m tenth_floor.data.market_data                 # all pairs, all TFs
        python -m tenth_floor.data.market_data BTCUSDT         # single pair
        python -m tenth_floor.data.market_data BTCUSDT ETHUSDT # multiple pairs
    """
    import sys

    from rich.console import Console
    from rich.table import Table

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    console = Console()
    console.print("\n[bold cyan]the-tenth-floor · Market Data Fetcher[/bold cyan]\n")

    # Parse CLI args
    args = sys.argv[1:]
    timeframes = _load_timeframes()
    fetcher = MarketDataFetcher()

    if args:
        pairs = args
        console.print(f"Pairs: [bold]{', '.join(pairs)}[/bold]")
    else:
        from tenth_floor.universe import load_universe

        pairs = load_universe().symbols(asset_class="crypto")
        console.print(f"Universe: [bold]{len(pairs)}[/bold] crypto pairs from config/universe.json")

    console.print(f"Timeframes: [bold]{', '.join(timeframes)}[/bold]\n")

    # Fetch
    results = fetcher.fetch_universe(pairs=pairs, timeframes=timeframes)

    # Summary table
    table = Table(title="Fetch Results", show_lines=True)
    table.add_column("Pair", style="cyan", no_wrap=True)
    table.add_column("TF", style="green", no_wrap=True)
    table.add_column("Rows", justify="right")
    table.add_column("First Date", style="dim")
    table.add_column("Last Date", style="dim")
    table.add_column("Cache Size", justify="right", style="yellow")

    for symbol in results:
        for tf, df in results[symbol].items():
            if df.empty:
                table.add_row(symbol, tf, "0", "—", "—", "—")
                continue
            info = fetcher.cache_info(symbol, tf)
            table.add_row(
                symbol,
                tf,
                str(len(df)),
                info.get("first_ts", "—")[:10],
                info.get("last_ts", "—")[:16],
                f"{info.get('size_kb', 0)} KB",
            )

    console.print(table)
    console.print("\n[bold green]✓ Done.[/bold green] Cache dir:", str(fetcher._cache_dir))


if __name__ == "__main__":
    _cli_main()
