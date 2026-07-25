"""
Universe configuration loader for The Tenth Floor AI.

Single source of truth for the asset universe. All modules that need
universe data should import from here instead of reading universe.json
directly.

Usage::

    from tenth_floor.universe import load_universe, get_asset, get_class_config

    universe = load_universe()
    symbols = universe.symbols()                    # all 36 symbols
    crypto = universe.symbols(asset_class="crypto") # just crypto
    cfg = universe.class_config("crypto")           # class-level config
    asset = universe.asset("BTCUSDT")               # single asset info
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tenth_floor.config import CONFIG_DIR


@dataclass(frozen=True)
class AssetClassConfig:
    """Configuration for an asset class (crypto, equity, etf, commodity)."""

    name: str
    data_source: str  # "ccxt" or "yfinance"
    check_timeframe: str  # "4h" for crypto, "1d" for equities
    expiry_days: int  # 14 for crypto, 10 for equities
    entry_type: str  # "immediate" or "conditional_open"
    market_hours: str = "24/7"
    exchange: str | None = None
    market_type: str | None = None
    quote_currency: str | None = None


@dataclass(frozen=True)
class AssetEntry:
    """A single asset in the universe."""

    symbol: str
    asset_class: str
    sector: str


@dataclass
class Universe:
    """Loaded universe configuration with query methods."""

    assets: list[AssetEntry] = field(default_factory=list)
    class_configs: dict[str, AssetClassConfig] = field(default_factory=dict)
    max_per_sector: int = 1
    max_per_asset_class: int = 2
    max_featured_signals: int = 3

    def symbols(self, *, asset_class: str | None = None) -> list[str]:
        """Return symbols, optionally filtered by asset class."""
        if asset_class is None:
            return [a.symbol for a in self.assets]
        return [a.symbol for a in self.assets if a.asset_class == asset_class]

    def asset(self, symbol: str) -> AssetEntry | None:
        """Look up a single asset by symbol."""
        for a in self.assets:
            if a.symbol == symbol:
                return a
        return None

    def class_config(self, asset_class: str) -> AssetClassConfig:
        """Return the config for an asset class. Raises KeyError if not found."""
        return self.class_configs[asset_class]

    def asset_class_for(self, symbol: str) -> str | None:
        """Return the asset class for a symbol, or None if not found."""
        a = self.asset(symbol)
        return a.asset_class if a else None

    def sector_for(self, symbol: str) -> str | None:
        """Return the sector for a symbol, or None if not found."""
        a = self.asset(symbol)
        return a.sector if a else None

    def data_source_for(self, symbol: str) -> str | None:
        """Return 'ccxt' or 'yfinance' for a symbol."""
        ac = self.asset_class_for(symbol)
        if ac is None:
            return None
        return self.class_configs[ac].data_source

    def sector_map(self) -> dict[str, str]:
        """Return {symbol: sector} dict for all assets."""
        return {a.symbol: a.sector for a in self.assets}

    def asset_classes(self) -> list[str]:
        """Return list of asset class names."""
        return list(self.class_configs.keys())


def load_universe(path: Path | None = None) -> Universe:
    """Load and parse ``config/universe.json``.

    Parameters
    ----------
    path:
        Override the config file path (useful for testing).

    Returns
    -------
    Universe
        Parsed universe with query methods.
    """
    path = path or (CONFIG_DIR / "universe.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    # Parse asset class configs
    class_configs: dict[str, AssetClassConfig] = {}
    for name, cfg in data.get("asset_classes", {}).items():
        class_configs[name] = AssetClassConfig(
            name=name,
            data_source=cfg["data_source"],
            check_timeframe=cfg["check_timeframe"],
            expiry_days=cfg["expiry_days"],
            entry_type=cfg["entry_type"],
            market_hours=cfg.get("market_hours", "24/7"),
            exchange=cfg.get("exchange"),
            market_type=cfg.get("market_type"),
            quote_currency=cfg.get("quote_currency"),
        )

    # Parse assets
    assets: list[AssetEntry] = []
    for item in data.get("assets", []):
        assets.append(
            AssetEntry(
                symbol=item["symbol"],
                asset_class=item["asset_class"],
                sector=item["sector"],
            )
        )

    if not assets:
        raise ValueError(f"No assets found in {path}")

    return Universe(
        assets=assets,
        class_configs=class_configs,
        max_per_sector=data.get("max_per_sector", 1),
        max_per_asset_class=data.get("max_per_asset_class", 2),
        max_featured_signals=data.get("max_featured_signals", 3),
    )
