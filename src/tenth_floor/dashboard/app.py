"""
Admin Dashboard — Streamlit app for signal performance review.

Reads from ``data/playbook_history.db`` (read-only) and displays:

- KPI summary cards (total / open / win rate / avg R:R)
- Signal history table (sortable, filterable)
- Performance breakdown by conviction tier (when 30+ closed trades exist)
- Outcome distribution chart

Launch::

    streamlit run src/tenth_floor/dashboard/app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tenth_floor.dashboard.queries import (
    CLOSED_STATUSES,
    compute_tier_stats,
    load_signals,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="The Tenth Floor AI — Admin",
    page_icon="🔟",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Cached data load (30-second TTL)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30)
def _cached_load() -> pd.DataFrame:
    return load_signals()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("🔟 The Tenth Floor AI — Admin Dashboard")

df = _cached_load()

if df.empty:
    st.info("No signals in the database yet. Run the pipeline to generate signals.")
    st.stop()

# ---------------------------------------------------------------------------
# Filters (sidebar)
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

pairs = sorted(df["pair"].unique())
selected_pairs = st.sidebar.multiselect("Pair", pairs, default=pairs)

statuses = sorted(df["status"].unique())
selected_statuses = st.sidebar.multiselect("Status", statuses, default=statuses)

convictions = sorted(df["conviction"].dropna().unique())
selected_convictions = st.sidebar.multiselect("Conviction", convictions, default=convictions)

timeframes = sorted(df["timeframe"].unique())
selected_timeframes = st.sidebar.multiselect("Timeframe", timeframes, default=timeframes)

# Date range
min_date = df["report_date"].min()
max_date = df["report_date"].max()
if pd.notna(min_date) and pd.notna(max_date):
    date_range = st.sidebar.date_input(
        "Report date range",
        value=(min_date.date(), max_date.date()),
        min_value=min_date.date(),
        max_value=max_date.date(),
    )
else:
    date_range = None

# Apply filters
mask = (
    df["pair"].isin(selected_pairs)
    & df["status"].isin(selected_statuses)
    & df["conviction"].isin(selected_convictions)
    & df["timeframe"].isin(selected_timeframes)
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    mask = mask & (
        (df["report_date"].dt.date >= date_range[0])
        & (df["report_date"].dt.date <= date_range[1])
    )

filtered = df[mask]

# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

st.markdown("---")

closed = filtered[filtered["status"].isin(CLOSED_STATUSES)]
open_signals = filtered[filtered["status"].isin({"PENDING", "OPEN"})]
wins = closed[closed["status"] == "HIT_TP"]

col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("Total Signals", len(filtered))
col2.metric("Open / Pending", len(open_signals))
col3.metric("Closed", len(closed))

if len(closed) > 0:
    win_rate = len(wins) / len(closed) * 100
    col4.metric("Win Rate", f"{win_rate:.1f}%")
else:
    col4.metric("Win Rate", "—")

avg_rr = filtered["reward_risk"].mean()
col5.metric("Avg R:R", f"{avg_rr:.2f}" if pd.notna(avg_rr) else "—")

# ---------------------------------------------------------------------------
# Signal history table
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("Signal History")

display_cols = [
    "report_date", "pair", "timeframe", "conviction", "confidence_score",
    "entry_low", "entry_high", "stop_loss", "take_profit",
    "reward_risk", "suggested_risk_pct", "status",
    "outcome_price", "outcome_date", "strategy_rationale",
]
display_cols = [c for c in display_cols if c in filtered.columns]

st.dataframe(
    filtered[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "report_date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
        "pair": "Pair",
        "timeframe": "TF",
        "conviction": "Conviction",
        "confidence_score": st.column_config.NumberColumn("Confidence", format="%.2f"),
        "entry_low": st.column_config.NumberColumn("Entry Low", format="%.4f"),
        "entry_high": st.column_config.NumberColumn("Entry High", format="%.4f"),
        "stop_loss": st.column_config.NumberColumn("Stop Loss", format="%.4f"),
        "take_profit": st.column_config.NumberColumn("Take Profit", format="%.4f"),
        "reward_risk": st.column_config.NumberColumn("R:R", format="%.2f"),
        "suggested_risk_pct": st.column_config.NumberColumn("Risk %", format="%.1f%%"),
        "status": "Status",
        "outcome_price": st.column_config.NumberColumn("Exit Price", format="%.4f"),
        "outcome_date": st.column_config.DateColumn("Exit Date", format="YYYY-MM-DD"),
        "strategy_rationale": "Rationale",
    },
)

# ---------------------------------------------------------------------------
# Performance by conviction tier
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("Performance by Conviction Tier")

if len(closed) >= 30:
    tier_stats = compute_tier_stats(closed)
    tier_stats = tier_stats.rename(columns={
        "conviction": "Tier",
        "total": "Total",
        "wins": "Wins",
        "losses": "Losses",
        "expired": "Expired",
        "avg_rr": "Avg R:R",
        "avg_confidence": "Avg Confidence",
        "win_rate": "Win Rate %",
    })

    st.dataframe(tier_stats, use_container_width=True, hide_index=True)
else:
    remaining = max(0, 30 - len(closed))
    st.info(
        f"Performance breakdown requires 30+ closed trades. "
        f"Currently {len(closed)} closed — {remaining} more needed."
    )

# ---------------------------------------------------------------------------
# Outcome distribution
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("Outcome Distribution")

if len(closed) > 0:
    outcome_counts = closed["status"].value_counts()

    col_chart, col_table = st.columns([2, 1])

    with col_chart:
        st.bar_chart(outcome_counts)

    with col_table:
        st.dataframe(
            outcome_counts.reset_index().rename(
                columns={"status": "Outcome", "count": "Count"}
            ),
            hide_index=True,
        )
else:
    st.info("No closed signals yet. Run the outcome checker to resolve open signals.")

# ---------------------------------------------------------------------------
# MAE / MFE analysis (only when data exists)
# ---------------------------------------------------------------------------

has_mae = closed["max_adverse_excursion"].notna().any() if len(closed) > 0 else False
has_mfe = closed["max_favorable_excursion"].notna().any() if len(closed) > 0 else False

if has_mae or has_mfe:
    st.markdown("---")
    st.subheader("MAE / MFE Analysis")

    col_mae, col_mfe = st.columns(2)

    if has_mae:
        with col_mae:
            st.markdown("**Max Adverse Excursion** (worst drawdown from entry)")
            mae_data = closed[closed["max_adverse_excursion"].notna()][
                ["pair", "timeframe", "conviction", "status", "max_adverse_excursion"]
            ]
            st.dataframe(mae_data, use_container_width=True, hide_index=True)

    if has_mfe:
        with col_mfe:
            st.markdown("**Max Favourable Excursion** (best unrealised gain)")
            mfe_data = closed[closed["max_favorable_excursion"].notna()][
                ["pair", "timeframe", "conviction", "status", "max_favorable_excursion"]
            ]
            st.dataframe(mfe_data, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption(
    f"{len(df)} total signals  ·  "
    "Refresh: re-run this page or wait 30s"
)
