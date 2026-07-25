# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Install

```bash
pip install -e ".[dev]"          # editable install with dev deps (pytest, ruff, mypy, black)
```

## Commands

```bash
# Tests
pytest                                             # all tests (mocked, no LLM/network needed)
pytest tests/test_agents.py -v                     # single file
pytest tests/test_main.py::TestPipeline::test_name # single test
pytest -k "dedup" -v                               # by pattern

# Lint & type check
ruff check src/ tests/                 # lint (E, F, I, UP, B rules; line-length 100)
ruff check --fix src/                  # auto-fix
mypy src/tenth_floor/                  # type check (Python 3.12 target)

# Dashboard (the operational path — FastAPI + Vite in parallel)
./dashboard.sh                                # http://localhost:5173

# Headless pipeline (dev / debug only — dashboard Run button is the norm)
python -m tenth_floor.main                    # full universe
python -m tenth_floor.main BTCUSDT ETHUSDT    # specific pairs
python -m tenth_floor.main --dry-run          # no DB writes

# Outcome checker (resolves OPEN signals against real candles)
python -m tenth_floor.check_outcomes
python -m tenth_floor.check_outcomes --dry-run

# CLI helpers (see ./run.sh --help)
./run.sh --local                              # headless local pipeline run
./run.sh --outcomes-only                      # resolve OPEN signals
./run.sh --reset-db                           # wipe + recreate signal DB from schema.sql
```

## Architecture

**The Tenth Floor** is a personal multi-agent LLM research tool that produces daily swing-trade signals across a 20-asset universe (crypto, US equities, ETFs, commodities). It runs locally with Qwen3-32B-AWQ on vLLM. The operator triggers runs from a React dashboard — there is no cron and no scheduler, though a run that publishes signals does post them to Discord and Notion when those env vars are configured. See [plan.md](plan.md) for the original 365-day experiment plan (historical — the experiment stopped in April 2026); [ROADMAP.md](ROADMAP.md) for the V4 architecture history.

### Pipeline Flow (Phase 1.5: AI-First)

```
Data (ccxt/yfinance) ──→ TACalculator ──→ Indicators + Structural Levels
                                                    ↓
MacroAnalyst (1 LLM call) ──→ macro frame (regime, per-class impact)
                                                    ↓
Pre-screen (data-quality only, very permissive) ──→ up to 20 candidates
                                                    ↓
TradeAnalyst (1 LLM call per candidate) ──→ BUY proposals with entry/SL/TP/reasoning
                                                    ↓
Python validation (sanity checks) ──→ valid proposals
                                                    ↓
RiskReviewer (1 LLM call per proposal) ──→ approved signals with conviction
                                                    ↓
Signal cap (business rule) ──→ featured signals ──→ SignalLogger (dashboard reads SQLite)
```

### Hard Rules

- **AI-first, Python validates.** LLM agents decide entry/SL/TP and BUY/SKIP. Python computes TA indicators as input context, validates LLM output for sanity (SL < entry < TP, R:R math, price bounds), and enforces hard business rules.
- **Spot only, LONG only.** No futures, no margin, no leverage.
- **R:R >= 1.5 hard floor.** Business integrity rule — subscribers should never get a mathematically unfavorable trade. The LLM decides if a setup is good; Python confirms the math.
- **1d timeframe only.** Daily candles across all asset classes.
- **Signal cap per run** — 2 under `profiles/production.json`, 3 under `profiles/validation.json`, 5 from the base `risk_profile.json` default. Silence is the default.
- **Duplicate-safe re-runs.** `UNIQUE(pair, timeframe, report_date)` + `INSERT OR IGNORE`.
- **Symbol format:** `BTCUSDT` for crypto (no slash), standard tickers for equities (`AAPL`, `SPY`). Normalised once at data fetch. LLM symbol output is overridden with authoritative snapshot symbol.

### Agent Pattern

All agents use `agents/base.py`:
- `call_llm()` — provider-agnostic, routes to any OpenAI-compatible backend
- `parse_json_response()` — validates LLM output against Pydantic models
- `clean_json_response()` — strips Qwen3 `<think>` blocks and markdown fences
- LLM tracing via `langfuse.openai.OpenAI` wrapper (auto-instruments every call)

Three agents: MacroAnalyst (macro regime), TradeAnalyst (per-asset BUY/SKIP with LLM-chosen levels), RiskReviewer (portfolio-level approval + conviction). The LLM makes all trading decisions; Python validates output for sanity.

**Prompt philosophy:** Minimal prompts — role + context + output format only. No prescriptive trading rules, no "when to skip" checklists, no fixed confluence/risk factor lists. The LLM reasons freely using its own domain knowledge. Python's validation layer catches bad math. This approach scales better as models improve.

### Config

All config in `config/`. No secrets — those go in `.env` (see `.env.example`).

| File | Purpose |
|------|---------|
| `universe.json` | 20 assets across 4 classes + sector mapping |
| `risk_profile.json` | Conviction tiers, SL/TP params, confidence threshold, max signals |
| `models.yaml` | LLM provider routing + per-agent temp/token settings |
| `services.yaml` | External service URLs and cache settings |

**Config profiles:** `config/profiles/production.json` (confidence 0.65, max 2 signals) and `config/profiles/validation.json` (confidence 0.57, max 3 signals). Switch via `--profile validation|production`. Base `risk_profile.json` holds production defaults; profiles overlay specific values.

Path resolution: `config.py` walks up from CWD looking for `pyproject.toml`. Override with `TENTH_FLOOR_ROOT` env var.

### Typed Contracts

`data/models.py` defines every inter-module boundary as a frozen Pydantic v2 model. The layers are: Data (`OHLCVBar`, `SentimentSnapshot`) → Features (`TAIndicators`, `PairSnapshot`) → Agent outputs (`MacroSignal`, `TradeProposal`, `ReviewedSignal`, `PlaybookEntry`). No free-form numeric fields in agent outputs.

### Signal Lifecycle

`OPEN` (fills immediately at the snapshot price) → `HIT_TP` / `HIT_SL` / `EXPIRED`.
There is no PENDING tier and no entry zone — both were removed in `db/migrations/003_drop_entry_zone_and_pending.sql`.
Expiry and check timeframe are per asset class (`config/universe.json`): crypto walks 4h candles and expires at 14 days, everything else walks 1d and expires at 10. SL wins on same-candle ambiguity (conservative). MAE/MFE tracked per signal.

### Compatibility Note

`features/ta_calculator.py` patches `numpy.isnan` before importing pandas-ta for Python 3.13 + pandas 3.x compatibility. The patch is scoped and restored immediately after import.

## Project Status

**The project is stopped.** It ran as a live forward test for roughly one week in April 2026 and was never resumed; the repo is public as a portfolio piece. See [README.md](README.md#results) for what the run produced (and why the sample is too small to conclude anything). Treat the phase notes below as a record of what was built, not a queue of work.

**V4 was the active plan** (approved 2026-03-30). Multi-asset universe + AI-first architecture. See [ROADMAP.md](ROADMAP.md) for the full plan.

V3 is complete. V2 is complete.

**V4 Phase 1 (complete):** Multi-asset foundation — universe restructuring, YFinanceDataFetcher, class-leader gates, honest R:R, multi-source outcome checker.

**V4 Phase 1.5 (complete — 2026-04-07):** AI-first signal generation — MacroAnalyst (macro regime), TradeAnalyst (LLM picks entry/SL/TP), RiskReviewer (portfolio-level LLM reasoning), Python validation layer. Minimal prompts (role + output format only, no prescriptive rules). All V3 agents and mechanical gates deleted. Verified end-to-end with live market data. 176 tests pass.

**V4 Phase 2 (partially delivered, then stopped):** Signal quality.
- **2A: Infrastructure (delivered)** — Docker Compose (5090 deployment), hardware profiles, per-proposal RiskReviewer (replaced the batch call), TradeAnalyst prompt refinement, macro-aware ranking.
- **2B: Context Enrichment** — RSS feeds (8 sources), 10Y yield via FRED, earnings calendar, asset-specific news injection. Requires 5090 token budget.
- **2C: Equities-Specific** — Conditional entry zones, two-pass scheduling, market calendar in outcome checker.
