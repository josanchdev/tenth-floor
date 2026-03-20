# THE TENTH FLOOR AI — Project Vision & Pivot Brief
# Read this fully before doing anything. Do not write any code yet.

## What This Project Is

You are the Lead Python Architect on "The Tenth Floor AI" — a
glass-box quantitative crypto research desk. The system fetches
Binance spot OHLCV data, computes technical indicators
deterministically in Python, runs a multi-agent LLM pipeline
(Quant, Sentiment, Strategy, Risk), and publishes daily swing-trade
signals to a paid Discord community via Webhook.

Every signal includes full mathematical and agentic reasoning.
Subscribers execute trades manually on their own accounts.
The system never touches money, never places orders, never manages
a budget.

## The Business Model

This is a commercial signal provider for a premium paid Discord.
Signals must be institutional-grade, fully explained, and
reproducible. The system's credibility is its product.
Code quality, observability, and correctness are non-negotiable.

## The Pivot We Are Executing

The existing codebase was built as a PERSONAL trading tool managing
a fixed €100 budget. We are pivoting to a PUBLIC signal provider.
This means:

REMOVING:
- All EUR budget management (positions.json, cash_eur, portfolio_value_eur)
- Per-trade EUR sizing math (€33.30 per position logic)
- HTML/Markdown report files written to disk
- The personal Streamlit trade management UI (close/edit positions)

ADDING:
- SQLite database at data/playbook_history.db for signal logging
- Discord Webhook output (one consolidated daily embed)
- Conviction-tiered risk suggestions (suggested_risk_pct) replacing
  EUR position sizing
- Admin Streamlit dashboard for signal performance review
- Weekly autoresearch report (human-in-the-loop, not auto-rewriting)

## Conviction Tier Logic (RiskAgent)

This replaces ALL EUR portfolio logic:
- confidence >= 0.80 → conviction = "high"    → suggested_risk_pct = 0.02
- confidence >= 0.65 → conviction = "standard" → suggested_risk_pct = 0.01
- confidence < 0.65  → SKIP, do not publish

## Canonical Symbol Format

ALWAYS 'BTCUSDT' (no slash). Normalisation happens ONCE at
MarketDataFetcher._normalise_symbol(). Every downstream module —
agents, DB logger, Discord notifier, SQLite storage — receives
and stores symbols in this format. This is a hard contract.

## SQLite Schema (exact, do not deviate)

CREATE TABLE signals (
    signal_id          TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    report_date        TEXT NOT NULL,
    pair               TEXT NOT NULL,
    timeframe          TEXT NOT NULL,
    direction          TEXT NOT NULL,
    conviction         TEXT NOT NULL,
    confidence_score   REAL NOT NULL,
    entry_low          REAL NOT NULL,
    entry_high         REAL NOT NULL,
    stop_loss          REAL NOT NULL,
    take_profit        REAL NOT NULL,
    reward_risk        REAL NOT NULL,
    suggested_risk_pct REAL NOT NULL,
    strategy_rationale TEXT,
    status             TEXT NOT NULL DEFAULT 'OPEN',
    outcome_price      REAL,
    outcome_date       TEXT,
    max_adverse_excursion  REAL,
    max_favorable_excursion REAL,
    langfuse_trace_id  TEXT
);

## Discord Embed Spec (exact structure)

One consolidated embed per daily run. Not one per signal.

Title:    "🔟 THE TENTH FLOOR — {date}"
Color:    0x00ff88 if any approved signals, 0x888888 if none
Fields per approved signal:
  - "{pair} · LONG · {CONVICTION TIER}"
  - "Entry: {entry_low} – {entry_high}"
  - "Stop: {stop_loss}  |  Target: {take_profit}"
  - "R:R: {reward_risk} · Risk: {suggested_risk_pct*100}%"
  - "Rationale: {strategy_rationale}"
Footer:   "Open signals in DB: {n}  |  Powered by The Tenth Floor AI"
Rate limit: sleep 1 second between webhook sends if ever sending
            multiple embeds in future.

## Known Limitations (document but do not fix)

1. Entry zone in strategy_agent.py uses ±0.5% of spot price.
   This is a placeholder. Real implementation should use
   ATR-based support levels. Tag with # KNOWN LIMITATION comment.

2. QuantAgent confidence score is LLM-generated, not statistically
   calibrated. Tag with # KNOWN LIMITATION comment.

## V2 is complete when:
- Tasks 11-14 are committed and tested
- At least one real signal has been posted to Discord
- SQLite is logging correctly
- Admin dashboard shows signal history
V2.1 begins when you have 30+ closed trades in the DB

## Locked Files — DO NOT MODIFY

- src/crypto_swing_copilot/agents/quant_agent.py
- src/crypto_swing_copilot/agents/sentiment_agent.py
- src/crypto_swing_copilot/agents/strategy_agent.py

## What To Do With This Brief

Read it. Confirm you understand the pivot by summarising:
1. What the system outputs (Discord + SQLite, not files)
2. What replaces EUR sizing (conviction tiers + suggested_risk_pct)
3. What the canonical symbol format is
4. Which files are locked

Also read the full existing codebase before responding.
Pay particular attention to:
- src/crypto_swing_copilot/agents/risk_agent.py (primary target for Task 11)
- src/crypto_swing_copilot/data/models.py (PlaybookEntry schema)
- config/risk_profile.json (config to update)
- tests/test_agents.py (tests that will break and need updating)

Do not write any code yet. Wait for my next instruction.
