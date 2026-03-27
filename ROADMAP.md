# V3 Roadmap — The Tenth Floor AI

> Started: 2026-03-26. This is the working implementation plan for V3.
> V2 is feature-complete (144 tests, full pipeline, Discord + SQLite + dashboard).
> V3 focuses on **diagnostics, testability, and commercial readiness**.
>
> **Tier 1 is complete** (2026-03-27). See below for details.

---

## Context

V2 has been running daily for 5 days with zero signals published.
This is the system working as designed — 7 sequential filtering gates
kill proposals in bearish/sideways markets. But a signal service that
never signals cannot be tested, improved, or sold.

V3 addresses three problems:
1. **Blindness** — no structured data on WHY signals die
2. **Inability to iterate** — can't test threshold changes without waiting 30+ days
3. **Fragility** — no retries, no failure alerts, no config safety

---

## Tier 1 — Fix the Core Problem ✓

*Get signals flowing. Know why they don't.*

All 6 items implemented 2026-03-27. Additional improvements:
- **RSI bullish divergence detection** added to `TACalculator` + `TAIndicators`
- **Capitulation bypass** in Gate 1: F&G rising from extreme fear + RSI divergence
  allows `STRONG_DOWNTREND` pairs through (conservative — co-occurrence is rare)
- **StrategyAgent prompt** updated with CAPITULATION SETUPS section and new
  confluence factors (RSI divergence, capitulation reversal)
- **Backtester** updated with historical F&G data fetch + capitulation bypass
  simulation + `--no-capitulation` flag
- **Config profiles** validated via backtesting: thresholds 0.50–0.57 produce
  identical results (7-signal discrete steps), so validation profile uses 0.57

### 1.1 Pipeline Diagnostics & Funnel Report

New SQLite table `pipeline_runs` tracking per-run gate statistics:
- `run_date`, `pairs_analyzed`
- `killed_trend_gate`, `killed_strategy_skip`, `killed_volume_gate`
- `killed_rs_gate`, `killed_rr_gate`, `killed_confidence_gate`
- `proposals_generated`, `approved`, `published`

Post funnel summary to Discord after every run (including zero-signal days):
```
Pipeline Funnel — 2026-03-26
  13 pairs analysed
   5 killed at trend regime gate
   3 killed at strategy (SKIP)
   2 killed at BTC relative strength
   2 killed at R:R < 2.0
   1 killed at confidence < 0.50
   0 approved
```

**Why:** Without this, threshold tuning is blind guesswork.

### 1.2 Historical Replay / Backtester

Replay the deterministic gates (no LLM needed) against 90 days of
cached OHLCV. Answer questions like:
- "If I lower R:R minimum to 1.8, how many more signals per month?"
- "If I remove the volume gate, what changes?"
- "Which gate kills the most signals?"

CLI: `python -m crypto_swing_copilot.backtest --days 90 --profile validation`

**Why:** Fastest way to calibrate thresholds without waiting 30 days.

### 1.3 Retry Logic for LLM Calls

`call_llm()` currently makes a single attempt with no timeout. One
vLLM hiccup = one silently lost pair.

Fix: configure the OpenAI client with built-in retries and timeout:
```python
OpenAI(base_url=..., api_key=..., max_retries=3, timeout=30.0)
```

Add `timeout` and `max_retries` fields to `config/models.yaml`.

**Why:** Transient failures should not silently kill signals.

### 1.4 Fix Stale Prompts

Current issues:
- QuantAgent example output still says `"timeframe": "4h"` — pipeline is 1d only
- StrategyAgent example also references 4h
- QuantAgent `confidence` field is computed by LLM but mostly ignored
  (Python's `trend_score` overrides it) — prompt doesn't explain this
- signals.md references stale confidence threshold (0.55 vs actual 0.50)

Fix all prompts to reflect 1d-only reality. Add a note in QuantAgent
prompt that `confidence` is informational context, not the gating score.

**Why:** Stale prompt examples confuse the model and waste reasoning tokens.

### 1.5 Config Profiles (Validation / Production)

Current `risk_profile.json` has `_comment` fields saying "VALIDATION MODE:
restore after 30+ closed trades." No mechanism to switch.

Solution:
- `config/profiles/validation.json` — current relaxed values (confidence 0.50, max 3 signals)
- `config/profiles/production.json` — tight values (confidence 0.65, max 2 signals)
- CLI flag: `--profile production|validation` (default: production)
- `risk_profile.json` becomes a symlink or is loaded by profile name

**Why:** Eliminates "remember to change this back" as a failure mode.

### 1.6 Failure Alerting

If the pipeline crashes, nobody knows until manual check.

Solution: wrap `run_pipeline()` in try/except that posts an error embed
to Discord on unhandled exceptions. Use the existing `DiscordNotifier`
with an error-specific embed (red, with traceback summary).

**Why:** A commercial service cannot silently fail.

---

## Tier 2 — Commercial Grade

*Make it maintainable, testable, and safe to iterate on.*

### 2.1 Langfuse Prompt Management

Move all 4 agent system prompts to Langfuse:
- `langfuse.get_prompt("quant-agent", label="production")` at runtime
- Edit prompts from Langfuse UI, version history, instant rollback
- A/B test via label ("prod-a" vs "prod-b")
- **Hardcoded fallback**: if Langfuse unreachable, use local prompt strings

Migrate one agent first (QuantAgent), run for a week, then migrate the rest.

**Why:** Iterate on prompts without code changes or deploys.

### 2.2 DeepEval Signal Quality Tests

Write 15-20 test cases for LLM output quality:
- "Given strong downtrend indicators, QuantAgent must NOT classify as uptrend"
- "Given bullish indicators, StrategyAgent must NOT return action=skip"
- "RiskAgent reasoning must reference the actual rejection rule"

**Important caveat:** Using Qwen3-32B to judge its own output is
circular and unreliable. Use deterministic assertions on structured
fields (trend_regime, action, direction) rather than LLM-as-judge
metrics. Expand the existing pytest suite, not necessarily DeepEval.

**Why:** Regression-proof prompt changes. Only valuable once producing signals.

### 2.3 Structured Logging

JSON-formatted log lines for machine parsing:
```json
{"ts": "2026-03-26T00:15:00Z", "agent": "quant", "pair": "BTCUSDT", "gate": "trend_regime", "decision": "SKIP", "reason": "strong_downtrend"}
```

Enables `jq` analysis of historical runs. Complement (not replace)
human-readable logs.

**Why:** Debug pipeline behaviour across weeks of runs.

### 2.4 GitHub Actions CI

Workflow running on every push:
- `pytest` (all 144+ tests)
- `ruff check src/ tests/`
- `mypy src/crypto_swing_copilot/`

**Why:** Catch regressions before they reach the pipeline.

### 2.5 Richer Sentiment Sources

Add 3-4 more RSS feeds to `SentimentFetcher`:
- The Block, CoinTelegraph, Decrypt, DeFi Llama blog

Same simple `feedparser` pipeline. No vector DB, no GDELT, no
trafilatura. More headlines = richer context for SentimentAgent.

**Why:** 80% of the value of a full news pipeline at 5% of the effort.

### 2.6 DB Migration System

Versioned SQL migration files:
```
db/migrations/001_initial.sql
db/migrations/002_pipeline_runs.sql
db/migrations/003_...
```

Applied on startup with a `schema_version` table. Replaces the current
`CREATE TABLE IF NOT EXISTS` approach.

**Why:** Safe schema evolution without `--reset-db`.

---

## Tier 3 — Polish

*Nice to have. Not urgent.*

### 3.1 Multi-Model Configuration

Per-agent model override in `models.yaml` (infrastructure exists, unused):
- Smaller model for QuantAgent (classification, doesn't need 32B)
- Fallback chain: if primary fails 3x, try secondary model/provider

### 3.2 Pre-commit Hooks

`.pre-commit-config.yaml` with ruff + mypy + trailing whitespace.
Catch lint issues before commit.

### 3.3 Docker Compose

`docker-compose.yaml` with vLLM + pipeline services.
Reproducible deployment, easier to move to a server.

### 3.4 Grafana + Prometheus for vLLM

vLLM exposes `/metrics` natively. Add a Grafana dashboard for
GPU temp, VRAM, inference latency, queue depth.

---

## Tier 4 — Deferred (V4+)

These were evaluated and intentionally skipped for V3.

| Tool | Why deferred |
|---|---|
| **Mem0** | Batch pipeline, not conversational. SQLite IS the memory. Vector DB adds complexity for marginal gain in this architecture. |
| **Prefect** | 30-second pipeline doesn't need an orchestration server. Retries + Discord alerting cover the real needs. |
| **GDELT + trafilatura + vector DB** | Full news ingestion pipeline is a second project. More RSS feeds gets 80% of the value. |
| **LangGraph / Haystack** | Current 4-agent sequential flow is simple and works. Framework adds boilerplate, not value. |
| **Fine-tuning (Unsloth/TRL)** | Need 1000+ labeled signals. Build the data first. |
| **promptfoo** | Acquired by OpenAI (March 2026), Node.js (not Python), 2x inference cost for A/B tests. Langfuse prompt management covers this. |
| **Backtrader/FINSABER** | Custom backtester is simpler for this specific signal format. These expect traditional strategies, not LLM signals. |

---

## Known Limitations (carried from V2)

These are documented but intentionally unaddressed in V3:

1. **QuantAgent confidence is not calibrated** — trend_score replaced it
   for gating, but neither is validated against historical win rates.
   Revisit at 30+ closed trades.

2. **No per-pair sentiment** — all 13 pairs share the same F&G value
   and risk narrative. No sector differentiation (BTC vs DeFi vs L1).
   Planned for V4.

3. **BTC correlation guard is a heuristic** — not a statistical
   correlation measure. Covers the main failure mode but doesn't account
   for varying correlation strengths. Planned for V4.

---

## Completion Criteria

V3 is complete when:
- [x] Pipeline funnel report posts to Discord every run
- [x] Backtester can replay 90 days and produce a gate-kill summary
- [x] LLM calls retry on transient failure (max_retries=3, timeout=30s)
- [x] All agent prompts reference 1d only, no stale 4h examples
- [x] `--profile` flag switches between validation/production configs
- [x] Pipeline failures post error embed to Discord
- [ ] At least one agent's prompt lives in Langfuse with hardcoded fallback
- [ ] CI runs pytest + ruff + mypy on every push
- [ ] Sentiment uses 4+ RSS feeds
- [ ] DB migrations replace `CREATE TABLE IF NOT EXISTS`
