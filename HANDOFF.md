# Handoff Note — V2 Pivot Progress

Generated: 2026-03-20

## 1. What Is Complete

### Committed — Task 11 (commit `be4c7fb`)
Full V2 pivot of the agent layer and its contracts:

- **`config/risk_profile.json`** — removed V1 EUR budget fields; added `conviction_tiers` (high: 0.80/2%, standard: 0.65/1%)
- **`config/services.yaml`** — removed `reporting` section; added `discord` and `database` sections
- **`.env.example`** — added `DISCORD_WEBHOOK_URL`; updated header
- **`data/models.py`** — `PlaybookEntry` now has `confidence_score`, `conviction`, `suggested_risk_pct` replacing `position_size_pct`
- **`agents/risk_agent.py`** — full V2 rewrite: `run()` takes `list[tuple[SetupProposal, float]]`; removed all EUR logic; new `_compute_verdict()` and `_resolve_conviction()`; updated module docstring
- **`positions.json`** — deleted
- **`pyproject.toml`** — `jinja2` → `requests`; entrypoint updated; description updated
- **`tests/test_agents.py`** — `TestRiskAgent` fully rewritten for V2 interface; V1-only tests deleted; two new tests added

### Committed — Pre-Task 11 quality fixes (commit `1e60a93`)
- `config.py` — central path resolver created; all 4 consumers updated to import from it
- `import re` moved to top-level in `risk_agent.py`
- `PlaybookVerdict.REDUCED` removed from `models.py`
- `.gitignore` updated; `db/schema.sql` created with VISION.md DDL
- Dead `load_spot_config` import removed from `risk_agent.py`

### Committed — Documentation (post-Task 11)
- `README.md` — rewritten for V2 product
- `docs/architecture.md` — Mermaid flowchart, module reference table, design rules, pending task list
- `docs/signals.md` — conviction tiers, PlaybookEntry fields, price level math, rejection reasons, Discord embed spec, SQLite schema
- `KNOWN_LIMITATIONS.md` — entry zone placeholder (§1), confidence calibration (§2)

### Test coverage
44 tests passing across 4 files. All mocked — no network calls, no API keys required.

---

## 2. Commit History

```
be4c7fb feat: V2 pivot — replace EUR portfolio logic with conviction tiers   ← Task 11
1e60a93 fix: surgical code quality fixes pre-V2 pivot
3f94ca7 docs: add VISION.md — V2 Discord Signal Provider brief
```

Branch `main` is ahead of `origin/main` by 3 commits (not pushed).

---

## 3. What Is Next

### Task 11.5 — Additional test coverage (Items 25–28)

These were deferred after Task 11 to keep that commit focused:

- **Item 27**: `tests/test_market_data.py` — `_normalise_symbol()` and `_detect_gaps()` coverage
- **Item 25**: `tests/test_agents.py` — RiskAgent LLM fallback path (simulate `call_gemini` raising, assert Python-generated reasons survive)
- **Item 26**: `tests/test_agents.py` — multi-proposal run (2 proposals at different confidence levels, assert correct tier assignment for each)
- **Item 28**: `tests/test_pipeline_smoke.py` — end-to-end smoke test through the full agent chain with mocked Gemini

### Task 12 — SQLite signal logger

Create `src/crypto_swing_copilot/db/signal_logger.py`:
- `SignalLogger.log(entry: PlaybookEntry)` — insert approved signal
- Apply `db/schema.sql` DDL on first run (`CREATE TABLE IF NOT EXISTS`)
- `signal_id` = `{pair}_{report_date}_{uuid4()[:8]}`
- Only approved signals are logged (rejected entries are not stored)
- Read path: `open_signal_count()` → used by Discord embed footer

### Task 13 — Discord webhook notifier

Create `src/crypto_swing_copilot/notifications/discord_notifier.py`:
- `DiscordNotifier.post(entries: list[PlaybookEntry], open_count: int)` — posts one embed
- Embed spec: see `docs/signals.md` and `VISION.md §Discord Embed Spec`
- `DISCORD_WEBHOOK_URL` from env; no-op with warning if unset
- 1-second sleep between sends if ever extended to multiple embeds

### Task 14 — Admin dashboard

Create `src/crypto_swing_copilot/dashboard/app.py` (Streamlit):
- Signal history table (sortable by date, pair, conviction)
- Outcome tracking (mark signals as HIT_TP / HIT_SL / EXPIRED)
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

1. **`max_open_positions` removed from `risk_profile.json`** — V2 is a signal provider, not a portfolio manager. If a concurrent-signal cap is needed, it belongs in the orchestrator/DB layer.

2. **`RiskAgent.run()` interface is `list[tuple[SetupProposal, float]]`** — explicit, ordering-safe; the orchestrator naturally has both values together.

3. **`_resolve_conviction()` returns `("none", 0.0)` for sub-threshold confidence** — defensive default; will never appear in published signals since `_compute_verdict` rejects them first.

4. **`jinja2` replaced by `requests`** — V1 used Jinja2 for HTML reports; V2 needs `requests` for Discord webhook POST.

5. **`run-daily` entrypoint is a placeholder** — points to `crypto_swing_copilot.main:main` which does not exist yet. Will be created with the orchestrator.
