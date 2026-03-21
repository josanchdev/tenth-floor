"""
Dashboard data queries — pure SQL + pandas, no Streamlit dependency.

Reads from ``data/playbook_history.db`` and returns DataFrames for the
admin dashboard.  Testable without a Streamlit runtime.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from crypto_swing_copilot.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "playbook_history.db"

CLOSED_STATUSES = {"HIT_TP", "HIT_SL", "EXPIRED"}


def load_signals(db_path: Path | None = None) -> pd.DataFrame:
    """Load all signals from SQLite into a DataFrame.

    Parameters
    ----------
    db_path:
        Override the database file path.  Defaults to the project's
        ``data/playbook_history.db``.
    """
    path = db_path or DB_PATH
    if not path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(str(path))
    try:
        df = pd.read_sql_query("SELECT * FROM signals ORDER BY created_at DESC", conn)
    finally:
        conn.close()

    if df.empty:
        return df

    for col in ("created_at", "entered_at", "outcome_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")

    return df


def compute_tier_stats(closed: pd.DataFrame) -> pd.DataFrame:
    """Compute win/loss/expired stats grouped by conviction tier.

    Parameters
    ----------
    closed:
        DataFrame filtered to closed signals only (HIT_TP, HIT_SL, EXPIRED).
    """
    if closed.empty:
        return pd.DataFrame()

    stats = (
        closed.groupby("conviction")
        .agg(
            total=("status", "size"),
            wins=("status", lambda s: (s == "HIT_TP").sum()),
            losses=("status", lambda s: (s == "HIT_SL").sum()),
            expired=("status", lambda s: (s == "EXPIRED").sum()),
            avg_rr=("reward_risk", "mean"),
            avg_confidence=("confidence_score", "mean"),
        )
        .reset_index()
    )
    stats["win_rate"] = (stats["wins"] / stats["total"] * 100).round(1)
    return stats
