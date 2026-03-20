# Handoff Note — V2 Pivot Progress

Generated: 2026-03-20

## 1. What Is Complete

### Committed — Task 12: SQLite signal logger + outcome checker (commit `cc4a85d`)

Two new components for signal persistence and outcome tracking:

- **`db/schema.sql`** — updated: status default `PENDING` (was `OPEN`), new `entered_at` column for PENDING → OPEN transition timestamp
- **`db/signal_logger.py`** — `SignalLogger` class: `log()` inserts approved signals with status PENDING, `open_signal_count()` counts PENDING+OPEN, `get_active_signals()` returns signals for outcome checking, `update_signal()` with column-whitelist safety. Signal ID format: `{pair}_{date}_{uuid[:8]}`
- **`check_outcomes.py`** — standalone script (no LLM calls). Walks 4h candles chronologically for each active signal. PENDING → OPEN when candle low enters entry zone. SL/TP detection on candle high/low (SL-first on same-candle ambiguity). MAE/MFE tracked during walk. 14-day expiry. Groups signals by pair to minimise fetches. `--dry-run` flag for preview.
- **`tests/test_signal_logger.py`** — 8 tests: schema init, log/skip, counts, updates, column-whitelist rejection, Langfuse trace
- **`tests/test_check_outcomes.py`** — 11 tests: PENDING→OPEN, TP hit, SL hit, same-candle SL wins, entry+TP in same walk, MAE/MFE, pending expiry, open expiry, no-candles edge case, full DB integration

### Committed — Task 11.5: Local LLM backend (commit `610ad9c`)

Full migration from Google Gemini to local open-source inference:

- **`agents/base.py`** — `call_gemini()` → `call_llm()` with provider-agnostic routing via OpenAI-compatible API. New `clean_json_response()` strips Qwen3 `<think>` blocks and code fences. Removed `google-genai` dependency. `load_agent_config()` now merges `defaults` section from `models.yaml` with per-agent overrides. `LLM_BASE_URL` env var override.
- **All 4 agents** — updated imports, significantly improved prompts with explicit examples, structured scoring criteria, Qwen3-compatible formatting
- **`config/models.yaml`** — new `defaults` section with `provider: openai`, `base_url`, `model: qwen3-32b`
- **`pyproject.toml`** — `google-genai` → `openai`
- **`.env.example`** — replaced `GOOGLE_API_KEY` with `LLM_BASE_URL` and `OPENAI_API_KEY` docs
- **`tests/test_agents.py`** — all mock targets updated; added 7 new tests for base utilities

### Committed — Task 11 (commit `be4c7fb`)

Full V2 pivot of the agent layer and its contracts:

- **`config/risk_profile.json`** — removed V1 EUR budget fields; added `conviction_tiers`
- **`data/models.py`** — `PlaybookEntry` now has `confidence_score`, `conviction`, `suggested_risk_pct`
- **`agents/risk_agent.py`** — V2 rewrite: `run()` takes `list[tuple[SetupProposal, float]]`
- **`positions.json`** — deleted

### Committed — Pre-Task 11 quality fixes (commit `1e60a93`)

- `config.py` — central path resolver; all consumers updated
- `PlaybookVerdict.REDUCED` removed from `models.py`
- `db/schema.sql` created with VISION.md DDL

### Documentation commits

- `65d06d7` — docs: update architecture and handoff for Task 12
- `c6d3205` — docs: add V2 documentation — architecture, signals, known limitations

### Test coverage

70 tests passing across 6 files. All mocked — no network calls, no LLM server required.

---

## 2. Uncommitted Changes

**`agents/base.py`** — Langfuse observability fix (in progress):
- Changed `from openai import OpenAI` → `from langfuse.openai import OpenAI`
- Removed `@observe(name="llm_call")` decorator from `call_llm()`
- The `langfuse.openai.OpenAI` wrapper auto-instruments every `create()` call as a Langfuse **generation** event (captures tokens, model, messages, latency)
- Agent-level `@observe` decorators on `run()` methods remain — gives two-level traces: agent span → LLM generation
- 70/70 tests still passing
- **This should be committed before starting Task 13**

---

## 3. Commit History

```
65d06d7 docs: update architecture and handoff for Task 12
cc4a85d feat: SQLite signal logger + candle-walk outcome checker (Task 12)
610ad9c feat: switch LLM backend to local Qwen3 32B via vLLM              ← Task 11.5
c6d3205 docs: add V2 documentation — architecture, signals, known limitations
be4c7fb feat: V2 pivot — replace EUR portfolio logic with conviction tiers ← Task 11
1e60a93 fix: surgical code quality fixes pre-V2 pivot
3f94ca7 docs: add VISION.md — V2 Discord Signal Provider brief
```

---

## 4. What Is Next

### Commit the Langfuse fix

The `base.py` change (§2 above) is tested and ready. Commit it before proceeding.

### Task 13 — Discord webhook notifier

Create `src/crypto_swing_copilot/notifications/discord_notifier.py`:
- `DiscordNotifier.post(entries: list[PlaybookEntry], open_count: int)` — posts one embed
- Embed spec: see `docs/signals.md` and `VISION.md §Discord Embed Spec`
- `DISCORD_WEBHOOK_URL` from env; no-op with warning if unset
- 1-second sleep between sends if ever extended to multiple embeds

### Task 14 — Admin dashboard

Create `src/crypto_swing_copilot/dashboard/app.py` (Streamlit):
- Signal history table (sortable by date, pair, conviction)
- Performance summary (win rate by tier once 30+ closed trades exist)

### Orchestrator (`main.py`)

Create `src/crypto_swing_copilot/main.py` after Task 13:
- Fetch OHLCV for all universe pairs
- Fetch sentiment snapshot (once, shared)
- For each pair: `SnapshotBuilder → QuantAgent → SentimentAgent → StrategyAgent`
- Collect `(SetupProposal, confidence)` pairs → `RiskAgent`
- `SignalLogger.log()` approved entries
- `DiscordNotifier.post()` consolidated embed

---

## 5. Decisions Made Not in VISION.md

1. **Local LLM inference** — switched from Google Gemini to Qwen3 32B via vLLM. Provider-agnostic `call_llm()` supports any OpenAI-compatible API. Config-only model switching.

2. **Langfuse integration uses `langfuse.openai.OpenAI`** — not `@observe` on the LLM call function. The OpenAI wrapper auto-captures token usage, model name, prompt content, and latency as a generation event. Agent-level `@observe` decorators remain for parent spans, giving two-level tracing.

3. **Signal lifecycle: PENDING → OPEN** — signals start as PENDING; only flip to OPEN when price enters the entry zone. This prevents counting unfilled signals as winners.

4. **Outcome tracking via candle walk** — `check_outcomes.py` walks 4h candles chronologically, checking high/low (not close). First condition met (TP or SL) wins. Conservative same-candle assumption (SL first).

5. **MAE/MFE tracked during candle walk** — recorded per-signal during outcome checking, not as a separate pass. Gives Task 15 (performance analytics) its data for free.

6. **14-day expiry** — swing trades unresolved after 14 calendar days are marked EXPIRED. Aligns with weekly swing timeframe.

7. **`entered_at` column added to schema** — not in original VISION.md DDL. Records the timestamp when PENDING flips to OPEN (candle entered entry zone). Needed for accurate signal duration tracking.

8. **`update_signal()` uses column whitelist** — only `status`, `entered_at`, `outcome_price`, `outcome_date`, `max_adverse_excursion`, `max_favorable_excursion` can be updated. Prevents accidental modification of immutable signal fields.

9. **Outcome checker groups by pair** — fetches candles once per pair, then processes all signals for that pair. Minimises Binance API calls when multiple signals exist for the same pair.

10. **`max_open_positions` removed from `risk_profile.json`** — V2 is a signal provider, not a portfolio manager.

11. **`RiskAgent.run()` interface is `list[tuple[SetupProposal, float]]`** — explicit, ordering-safe.

12. **VISION.md locked files section is outdated** — all four agent files have been unlocked and rewritten for Task 11.5 (local LLM). The lock is no longer in effect.
