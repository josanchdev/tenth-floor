# Handoff Note — V2 Pivot Progress

Generated: 2026-03-20

## 1. What Is Complete

### Committed — Task 11.5: Local LLM backend

Full migration from Google Gemini to local open-source inference:

- **`agents/base.py`** — `call_gemini()` → `call_llm()` with provider-agnostic routing via OpenAI-compatible API. New `clean_json_response()` strips Qwen3 `<think>` blocks and code fences. Removed `google-genai` dependency. `load_agent_config()` now merges `defaults` section from `models.yaml` with per-agent overrides. `LLM_BASE_URL` env var override.
- **`agents/quant_agent.py`** — updated imports; significantly improved prompt with explicit trend regime definitions, confidence scoring criteria, signal label catalogue, and example output.
- **`agents/sentiment_agent.py`** — updated imports; improved prompt with bias classification guidance, headline-shift rules, and example output.
- **`agents/strategy_agent.py`** — updated imports; improved prompt with explicit decision framework, confluence factor catalogue, and BUY/SKIP examples.
- **`agents/risk_agent.py`** — updated imports; uses `clean_json_response()` for LLM output cleanup instead of inline regex.
- **`config/models.yaml`** — new `defaults` section with `provider: openai`, `base_url`, `model: qwen3-32b`. Per-agent sections now only override temperature and max_output_tokens.
- **`pyproject.toml`** — `google-genai` → `openai`
- **`.env.example`** — replaced `GOOGLE_API_KEY` with `LLM_BASE_URL` and `OPENAI_API_KEY` docs
- **`tests/test_agents.py`** — all mock targets updated `call_gemini` → `call_llm`; added `TestCleanJsonResponse` (4 tests) and `TestParseJsonResponse` (3 tests)

### Committed — Task 11 (commit `be4c7fb`)

Full V2 pivot of the agent layer and its contracts:

- **`config/risk_profile.json`** — removed V1 EUR budget fields; added `conviction_tiers` (high: 0.80/2%, standard: 0.65/1%)
- **`config/services.yaml`** — removed `reporting` section; added `discord` and `database` sections
- **`data/models.py`** — `PlaybookEntry` now has `confidence_score`, `conviction`, `suggested_risk_pct`
- **`agents/risk_agent.py`** — V2 rewrite: `run()` takes `list[tuple[SetupProposal, float]]`; removed all EUR logic; `_compute_verdict()` and `_resolve_conviction()`
- **`positions.json`** — deleted
- **`pyproject.toml`** — `jinja2` → `requests`; entrypoint updated; description updated

### Committed — Pre-Task 11 quality fixes (commit `1e60a93`)

- `config.py` — central path resolver; all consumers updated
- `PlaybookVerdict.REDUCED` removed from `models.py`
- `.gitignore` updated; `db/schema.sql` created with VISION.md DDL

### Documentation (post-Task 11.5)

- `README.md` — rewritten for V2 + local LLM stack
- `docs/architecture.md` — Mermaid flowchart updated for vLLM/Qwen3, outcome checker node added, module reference updated
- `docs/signals.md` — signal lifecycle updated to `PENDING → OPEN → HIT_TP | HIT_SL | EXPIRED`, outcome checker design documented
- `KNOWN_LIMITATIONS.md` — updated Gemini references to LLM-generic

### Test coverage

51 tests passing across 4 files. All mocked — no network calls, no LLM server required.

---

## 2. Commit History

```
c6d3205 docs: add V2 documentation — architecture, signals, known limitations
be4c7fb feat: V2 pivot — replace EUR portfolio logic with conviction tiers   ← Task 11
1e60a93 fix: surgical code quality fixes pre-V2 pivot
3f94ca7 docs: add VISION.md — V2 Discord Signal Provider brief
```

Task 11.5 changes are staged but not yet committed.

---

## 3. What Is Next

### Task 12 — SQLite signal logger + outcome checker

Two components:

**`src/crypto_swing_copilot/db/signal_logger.py`**:
- `SignalLogger.log(entry: PlaybookEntry)` — insert approved signal with status `PENDING`
- Apply `db/schema.sql` DDL on first run (`CREATE TABLE IF NOT EXISTS`)
- `signal_id` = `{pair}_{report_date}_{uuid4()[:8]}`
- `open_signal_count()` → used by Discord embed footer

**`src/crypto_swing_copilot/check_outcomes.py`** (standalone script):
- Runs independently from main.py — no LLM calls
- For each PENDING/OPEN signal, fetch 4h candles since `created_at`
- PENDING → OPEN: when candle low ≤ entry_zone_high (price enters entry zone)
- Chronological candle walk: first of `candle.low ≤ SL` or `candle.high ≥ TP` wins
- Same-candle ambiguity: assume SL hit first (conservative)
- Record MAE and MFE during the walk
- 14-day expiry: OPEN signals older than 14 days → `EXPIRED`

### Task 13 — Discord webhook notifier

Create `src/crypto_swing_copilot/notifications/discord_notifier.py`:
- `DiscordNotifier.post(entries: list[PlaybookEntry], open_count: int)` — posts one embed
- Embed spec: see `docs/signals.md` and `VISION.md §Discord Embed Spec`
- `DISCORD_WEBHOOK_URL` from env; no-op with warning if unset

### Task 14 — Admin dashboard

Create `src/crypto_swing_copilot/dashboard/app.py` (Streamlit):
- Signal history table (sortable by date, pair, conviction)
- Performance summary (win rate by tier once 30+ closed trades exist)

### Orchestrator (`main.py`)

Create `src/crypto_swing_copilot/main.py` after Tasks 12–13:
- Fetch OHLCV for all universe pairs
- Fetch sentiment snapshot (once, shared)
- For each pair: `SnapshotBuilder → QuantAgent → SentimentAgent → StrategyAgent`
- Collect `(SetupProposal, confidence)` pairs → `RiskAgent`
- `SignalLogger.log()` approved entries
- `DiscordNotifier.post()` consolidated embed

---

## 4. Decisions Made Not in VISION.md

1. **Local LLM inference** — switched from Google Gemini to Qwen3 32B via vLLM. Provider-agnostic `call_llm()` supports any OpenAI-compatible API. Config-only model switching.

2. **Signal lifecycle: PENDING → OPEN** — signals start as PENDING; only flip to OPEN when price enters the entry zone. This prevents counting unfilled signals as winners.

3. **Outcome tracking via candle walk** — `check_outcomes.py` walks 4h candles chronologically, checking high/low (not close). First condition met (TP or SL) wins. Conservative same-candle assumption (SL first).

4. **MAE/MFE tracked during candle walk** — recorded per-signal during outcome checking, not as a separate pass. Gives Task 15 (performance analytics) its data for free.

5. **14-day expiry** — swing trades unresolved after 14 calendar days are marked EXPIRED. Aligns with weekly swing timeframe.

6. **`max_open_positions` removed from `risk_profile.json`** — V2 is a signal provider, not a portfolio manager.

7. **`RiskAgent.run()` interface is `list[tuple[SetupProposal, float]]`** — explicit, ordering-safe.

8. **`_resolve_conviction()` returns `("none", 0.0)` for sub-threshold confidence** — defensive default; never appears in published signals.
