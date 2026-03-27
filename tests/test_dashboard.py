"""Tests for the admin dashboard data-loading and query logic.

These tests verify ``dashboard.queries`` (pure pandas + SQL, no
Streamlit dependency).  An in-memory SQLite DB is used — no disk I/O.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from crypto_swing_copilot.dashboard.queries import (
    CLOSED_STATUSES,
    compute_tier_stats,
    load_signals,
)

# ---------------------------------------------------------------------------
# Helpers — in-memory DB with test data
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    entered_at TEXT,
    report_date TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    entry_low REAL NOT NULL,
    entry_high REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    reward_risk REAL NOT NULL,
    suggested_risk_pct REAL NOT NULL,
    strategy_rationale TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    outcome_price REAL,
    outcome_date TEXT,
    max_adverse_excursion REAL,
    max_favorable_excursion REAL,
    langfuse_trace_id TEXT
);
"""

_SIGNALS = [
    (
        "BTCUSDT_2026-03-20_aaa11111", "2026-03-20T00:00:00", None,
        "2026-03-20", "BTCUSDT", "4h", "long", "high", 0.85,
        83000.0, 83500.0, 82000.0, 86000.0, 2.5, 0.02,
        "Strong uptrend", "PENDING", None, None, None, None, None,
    ),
    (
        "ETHUSDT_2026-03-20_bbb22222", "2026-03-20T00:00:00",
        "2026-03-20T04:00:00",
        "2026-03-20", "ETHUSDT", "1d", "long", "standard", 0.72,
        1900.0, 1920.0, 1850.0, 2000.0, 2.0, 0.01,
        "Moderate setup", "OPEN", None, None, 1870.0, 1960.0, None,
    ),
    (
        "SOLUSDT_2026-03-19_ccc33333", "2026-03-19T00:00:00",
        "2026-03-19T08:00:00",
        "2026-03-19", "SOLUSDT", "4h", "long", "high", 0.82,
        120.0, 121.0, 118.0, 126.0, 2.3, 0.02,
        "SOL breakout", "HIT_TP", 126.0, "2026-03-20", 119.0, 127.0, None,
    ),
    (
        "BNBUSDT_2026-03-18_ddd44444", "2026-03-18T00:00:00",
        "2026-03-18T04:00:00",
        "2026-03-18", "BNBUSDT", "1d", "long", "standard", 0.68,
        600.0, 605.0, 590.0, 625.0, 2.1, 0.01,
        "BNB range", "HIT_SL", 590.0, "2026-03-19", 588.0, 615.0, None,
    ),
    (
        "XRPUSDT_2026-03-17_eee55555", "2026-03-17T00:00:00",
        "2026-03-17T04:00:00",
        "2026-03-17", "XRPUSDT", "4h", "long", "standard", 0.66,
        0.55, 0.56, 0.53, 0.60, 1.9, 0.01,
        "XRP consolidation", "EXPIRED", 0.54, "2026-03-31", 0.52, 0.58, None,
    ),
]


@pytest.fixture()
def test_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite DB pre-loaded with test signals."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _SIGNALS,
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# load_signals
# ---------------------------------------------------------------------------


def test_load_signals_returns_all_rows(test_db: Path):
    df = load_signals(db_path=test_db)
    assert len(df) == 5


def test_load_signals_parses_dates(test_db: Path):
    df = load_signals(db_path=test_db)
    assert pd.api.types.is_datetime64_any_dtype(df["report_date"])
    assert pd.api.types.is_datetime64_any_dtype(df["created_at"])


def test_load_signals_missing_db(tmp_path: Path):
    df = load_signals(db_path=tmp_path / "nonexistent.db")
    assert df.empty


def test_load_signals_empty_table(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_SCHEMA)
    conn.commit()
    conn.close()

    df = load_signals(db_path=db_path)
    assert df.empty


# ---------------------------------------------------------------------------
# Data content
# ---------------------------------------------------------------------------


def test_signal_statuses(test_db: Path):
    df = load_signals(db_path=test_db)
    counts = df["status"].value_counts().to_dict()
    assert counts["PENDING"] == 1
    assert counts["OPEN"] == 1
    assert counts["HIT_TP"] == 1
    assert counts["HIT_SL"] == 1
    assert counts["EXPIRED"] == 1


def test_conviction_tiers(test_db: Path):
    df = load_signals(db_path=test_db)
    counts = df["conviction"].value_counts().to_dict()
    assert counts["high"] == 2
    assert counts["standard"] == 3


def test_closed_signals_have_outcomes(test_db: Path):
    df = load_signals(db_path=test_db)
    closed = df[df["status"].isin(CLOSED_STATUSES)]
    assert len(closed) == 3
    assert closed["outcome_price"].notna().all()


def test_mae_mfe_tracked(test_db: Path):
    df = load_signals(db_path=test_db)
    entered = df[df["entered_at"].notna()]
    assert len(entered) == 4
    assert entered["max_adverse_excursion"].notna().all()
    assert entered["max_favorable_excursion"].notna().all()


# ---------------------------------------------------------------------------
# compute_tier_stats
# ---------------------------------------------------------------------------


def test_win_rate_calculation(test_db: Path):
    df = load_signals(db_path=test_db)
    closed = df[df["status"].isin(CLOSED_STATUSES)]
    wins = closed[closed["status"] == "HIT_TP"]
    win_rate = len(wins) / len(closed) * 100
    assert win_rate == pytest.approx(33.3, abs=0.1)


def test_tier_stats_grouping(test_db: Path):
    df = load_signals(db_path=test_db)
    closed = df[df["status"].isin(CLOSED_STATUSES)]
    stats = compute_tier_stats(closed)

    assert "high" in stats["conviction"].values
    assert "standard" in stats["conviction"].values

    high = stats[stats["conviction"] == "high"]
    assert high.iloc[0]["wins"] == 1  # SOL HIT_TP


def test_tier_stats_empty():
    stats = compute_tier_stats(pd.DataFrame())
    assert stats.empty


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------


def test_filter_by_pair(test_db: Path):
    df = load_signals(db_path=test_db)
    btc = df[df["pair"] == "BTCUSDT"]
    assert len(btc) == 1
    assert btc.iloc[0]["timeframe"] == "4h"


def test_filter_by_status(test_db: Path):
    df = load_signals(db_path=test_db)
    active = df[df["status"].isin({"PENDING", "OPEN"})]
    assert len(active) == 2


def test_filter_by_date_range(test_db: Path):
    df = load_signals(db_path=test_db)
    recent = df[df["report_date"] >= "2026-03-19"]
    assert len(recent) == 3  # 3/19 SOL, 3/20 BTC + ETH
