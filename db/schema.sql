-- The Tenth Floor AI — Signal History Schema
-- Runtime location: data/playbook_history.db
-- This file is version-controlled; the .db file is git-ignored.
--
-- Status lifecycle: PENDING → OPEN → HIT_TP | HIT_SL | EXPIRED
--   PENDING: Signal published, price has not entered entry zone yet.
--   OPEN:    Price entered entry zone — signal is active.
--   HIT_TP:  Candle high reached take-profit.
--   HIT_SL:  Candle low reached stop-loss (assumed first on same-candle ambiguity).
--   EXPIRED: 14 calendar days with no TP or SL hit.

CREATE TABLE IF NOT EXISTS signals (
    signal_id                TEXT PRIMARY KEY,
    created_at               TEXT NOT NULL,
    entered_at               TEXT,              -- when PENDING → OPEN (candle entered entry zone)
    report_date              TEXT NOT NULL,
    pair                     TEXT NOT NULL,
    timeframe                TEXT NOT NULL,
    direction                TEXT NOT NULL,
    conviction               TEXT NOT NULL,
    confidence_score         REAL NOT NULL,
    entry_low                REAL NOT NULL,
    entry_high               REAL NOT NULL,
    stop_loss                REAL NOT NULL,
    take_profit              REAL NOT NULL,
    reward_risk              REAL NOT NULL,
    suggested_risk_pct       REAL NOT NULL,
    strategy_rationale       TEXT,
    status                   TEXT NOT NULL DEFAULT 'PENDING',
    outcome_price            REAL,
    outcome_date             TEXT,
    max_adverse_excursion    REAL,
    max_favorable_excursion  REAL,
    langfuse_trace_id        TEXT,

    -- Prevent duplicate signals for the same pair/timeframe on the same day.
    -- Re-running the pipeline is safe: the second insert is silently skipped.
    UNIQUE(pair, timeframe, report_date)
);

-- Pipeline funnel diagnostics — one row per daily run.
-- Tracks how many pairs were killed at each gate, enabling threshold tuning.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_date                 TEXT PRIMARY KEY,       -- YYYY-MM-DD
    created_at               TEXT NOT NULL,           -- ISO timestamp (UTC)
    pairs_analyzed           INTEGER NOT NULL DEFAULT 0,
    killed_trend_gate        INTEGER NOT NULL DEFAULT 0,
    killed_strategy_skip     INTEGER NOT NULL DEFAULT 0,
    killed_volume_gate       INTEGER NOT NULL DEFAULT 0,
    killed_rs_gate           INTEGER NOT NULL DEFAULT 0,
    killed_confidence_gate   INTEGER NOT NULL DEFAULT 0,
    killed_rr_gate           INTEGER NOT NULL DEFAULT 0,
    killed_btc_corr_gate     INTEGER NOT NULL DEFAULT 0,
    killed_signal_cap        INTEGER NOT NULL DEFAULT 0,
    proposals_generated      INTEGER NOT NULL DEFAULT 0,
    approved                 INTEGER NOT NULL DEFAULT 0,
    published                INTEGER NOT NULL DEFAULT 0,
    profile                  TEXT                     -- 'validation', 'production', or NULL
);
