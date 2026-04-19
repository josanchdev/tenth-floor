-- The Tenth Floor AI — Signal History Schema
-- Runtime location: data/playbook_history.db
-- This file is version-controlled; the .db file is git-ignored.
--
-- Status lifecycle: OPEN → HIT_TP | HIT_SL | EXPIRED
--   OPEN:    Signal published — fills immediately at the snapshot price.
--   HIT_TP:  Candle high reached take-profit.
--   HIT_SL:  Candle low reached stop-loss (assumed first on same-candle ambiguity).
--   EXPIRED: 14 calendar days with no TP or SL hit.
--
-- There is no PENDING tier and no entry zone. The pipeline runs once a day
-- and the operator acts on the price they see — a "wait for the zone" model
-- adds nothing for a daily cadence.

CREATE TABLE IF NOT EXISTS signals (
    signal_id                TEXT PRIMARY KEY,
    created_at               TEXT NOT NULL,
    report_date              TEXT NOT NULL,
    pair                     TEXT NOT NULL,
    timeframe                TEXT NOT NULL,
    direction                TEXT NOT NULL,
    conviction               TEXT NOT NULL,
    confidence_score         REAL NOT NULL,
    entry_price              REAL NOT NULL,
    stop_loss                REAL NOT NULL,
    take_profit              REAL NOT NULL,
    reward_risk              REAL NOT NULL,
    suggested_risk_pct       REAL NOT NULL,
    strategy_rationale       TEXT,
    status                   TEXT NOT NULL DEFAULT 'OPEN',
    outcome_price            REAL,
    outcome_date             TEXT,
    max_adverse_excursion    REAL,
    max_favorable_excursion  REAL,
    asset_class              TEXT NOT NULL DEFAULT 'crypto',
    langfuse_trace_id        TEXT,
    tier                     TEXT NOT NULL DEFAULT 'PUBLISHED',  -- PUBLISHED | SESSION
    notion_page_id           TEXT,                               -- Notion Signal Journal page ID

    -- Prevent duplicate signals for the same pair/timeframe on the same day.
    -- Re-running the pipeline is safe: the second insert is silently skipped.
    UNIQUE(pair, timeframe, report_date)
);

-- Pipeline funnel diagnostics — one row per daily run.
-- Phase 1.5: AI-first pipeline stages replace V3 mechanical gates.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_date                 TEXT PRIMARY KEY,       -- YYYY-MM-DD
    created_at               TEXT NOT NULL,           -- ISO timestamp (UTC)
    assets_in_universe       INTEGER NOT NULL DEFAULT 0,
    snapshots_built          INTEGER NOT NULL DEFAULT 0,
    pre_screen_passed        INTEGER NOT NULL DEFAULT 0,
    pre_screen_killed        INTEGER NOT NULL DEFAULT 0,
    trade_analyst_buy        INTEGER NOT NULL DEFAULT 0,
    trade_analyst_skip       INTEGER NOT NULL DEFAULT 0,
    trade_analyst_error      INTEGER NOT NULL DEFAULT 0,
    validation_passed        INTEGER NOT NULL DEFAULT 0,
    validation_failed        INTEGER NOT NULL DEFAULT 0,
    reviewer_approved        INTEGER NOT NULL DEFAULT 0,
    reviewer_rejected        INTEGER NOT NULL DEFAULT 0,
    signal_cap_killed        INTEGER NOT NULL DEFAULT 0,
    published                INTEGER NOT NULL DEFAULT 0,
    profile                  TEXT,                    -- 'validation', 'production', or NULL
    fear_greed_value         INTEGER,                 -- Fear & Greed index at run time
    macro_regime             TEXT                     -- MacroAnalyst regime assessment
);

-- Tweet auto-poster — tracks posted tweets for dedup and analytics.
CREATE TABLE IF NOT EXISTS posted_tweets (
    tweet_id         TEXT PRIMARY KEY,       -- X/Twitter post ID returned by API
    created_at       TEXT NOT NULL,          -- ISO timestamp (UTC) when posted
    report_date      TEXT NOT NULL,          -- which pipeline run this relates to
    tweet_text       TEXT NOT NULL,          -- the posted text
    tweet_type       TEXT NOT NULL,          -- funnel_report|market_commentary|philosophy|signal_day
    thread_tweets    TEXT,                   -- JSON array of thread continuation texts
    draft_file       TEXT,                   -- path to source draft file
    image_paths      TEXT,                   -- JSON array of image paths (future use)

    UNIQUE(report_date, tweet_type)
);
