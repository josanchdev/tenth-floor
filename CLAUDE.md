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
mypy src/tenth_floor/                  # type check (Python 3.11 target)

# Run pipeline
python -m tenth_floor.main                    # full universe
python -m tenth_floor.main BTCUSDT ETHUSDT    # specific pairs
python -m tenth_floor.main --dry-run           # no DB writes, no Discord post

# Outcome checker (resolves PENDING/OPEN signals against real candles)
python -m tenth_floor.check_outcomes
python -m tenth_floor.check_outcomes --dry-run

# Operational script (starts vLLM + runs pipeline + outcomes + DB backup)
./run.sh                    # full run
./run.sh --dry-run          # test run
./run.sh --reset-db         # wipe and recreate DB from schema.sql
./run.sh --outcomes-only    # skip pipeline, just check outcomes

# Tweet auto-poster
python -m tenth_floor.post_tweet                # draft + interactive post
python -m tenth_floor.post_tweet --draft-only   # generate draft only (no posting)
python -m tenth_floor.post_tweet 2026-03-28     # specific date

# Dashboard
streamlit run src/tenth_floor/dashboard/app.py
```

## Architecture

**The Tenth Floor AI** is a multi-agent LLM pipeline that publishes daily swing-trade signals to a paid Discord community. It runs locally with Qwen3-32B-AWQ on vLLM.

V4 is expanding from crypto-only to a multi-asset universe (~36 assets: crypto, US equities, ETFs, commodities) AND shifting to an AI-first architecture where the LLM makes trading decisions and Python validates. See [ROADMAP.md](ROADMAP.md) for the full plan.

### Pipeline Flow — Current (V3/Phase 1, being replaced in Phase 1.5)

```
Data sources (ccxt/yfinance) ──→ TACalculator ──→ SnapshotBuilder ──→ PairSnapshot
Sentiment (F&G, RSS)         ──────────────────────────────────────↗

PairSnapshot ──→ QuantAgent (trend + confidence)
              ──→ StrategyAgent (LONG proposal or SKIP, with entry/SL/TP)
SentimentSnapshot ──→ SentimentAgent (macro bias — runs once, shared)

All proposals ──→ 7 filtering gates ──→ RiskAgent (conviction tiers) ──→ PlaybookEntry[]
                                                                          │
                                                        ┌─────────────────┴──────────────┐
                                                   SignalLogger (SQLite)        DiscordNotifier (webhook)
```

### Pipeline Flow — Target (Phase 1.5: AI-First)

```
Data (ccxt/yfinance) ──→ TACalculator ──→ Indicators + Structural Levels
                                                    ↓
MacroAnalyst (1 LLM call) ──→ macro frame (regime, per-class impact)
                                                    ↓
Pre-screen (data-quality only, very permissive) ──→ ~30-36 candidates
                                                    ↓
TradeAnalyst (1 LLM call per candidate) ──→ BUY proposals with entry/SL/TP/reasoning
                                                    ↓
Python validation (sanity checks) ──→ valid proposals
                                                    ↓
RiskReviewer (1 LLM call, ALL proposals) ──→ approved signals with conviction
                                                    ↓
Signal cap (business rule) ──→ featured signals ──→ SignalLogger + Discord
```

### Hard Rules

- **AI-first, Python validates.** LLM agents decide entry/SL/TP and BUY/SKIP. Python computes TA indicators as input context, validates LLM output for sanity (SL < entry < TP, R:R math, price bounds), and enforces hard business rules.
- **Spot only, LONG only.** No futures, no margin, no leverage.
- **R:R >= 1.5 hard floor.** Business integrity rule — subscribers should never get a mathematically unfavorable trade. The LLM decides if a setup is good; Python confirms the math.
- **1d timeframe only.** Daily candles across all asset classes.
- **Max 3 featured signals per day** (production). Silence is the default.
- **Duplicate-safe re-runs.** `UNIQUE(pair, timeframe, report_date)` + `INSERT OR IGNORE`.
- **Symbol format:** `BTCUSDT` for crypto (no slash), standard tickers for equities (`AAPL`, `SPY`). Normalised once at data fetch. LLM symbol output is overridden with authoritative snapshot symbol.

### Agent Pattern

All agents use `agents/base.py`:
- `call_llm()` — provider-agnostic, routes to any OpenAI-compatible backend
- `parse_json_response()` — validates LLM output against Pydantic models
- `clean_json_response()` — strips Qwen3 `<think>` blocks and markdown fences
- LLM tracing via `langfuse.openai.OpenAI` wrapper (auto-instruments every call)

RiskAgent verdict logic is **pure Python** (no LLM for accept/reject decisions). The LLM only enriches the reasoning text for approved signals.

### Config

All config in `config/`. No secrets — those go in `.env` (see `.env.example`).

| File | Purpose |
|------|---------|
| `universe.json` | 26 Binance spot USDT pairs + sector mapping |
| `risk_profile.json` | Conviction tiers, SL/TP params, confidence threshold, max signals |
| `models.yaml` | LLM provider routing + per-agent temp/token settings |
| `services.yaml` | External service URLs and cache settings |

**Config profiles:** `config/profiles/production.json` (confidence 0.65, max 2 signals) and `config/profiles/validation.json` (confidence 0.57, max 3 signals). Switch via `--profile validation|production`. Base `risk_profile.json` holds production defaults; profiles overlay specific values.

Path resolution: `config.py` walks up from CWD looking for `pyproject.toml`. Override with `TENTH_FLOOR_ROOT` env var.

### Typed Contracts

`data/models.py` defines every inter-module boundary as a frozen Pydantic v2 model. The layers are: Data (`OHLCVBar`, `SentimentSnapshot`) → Features (`TAIndicators`, `PairSnapshot`) → Agent outputs (`QuantSignal`, `SentimentSignal`, `SetupProposal`, `PlaybookEntry`). No free-form numeric fields in agent outputs.

### Signal Lifecycle

`PENDING` → `OPEN` (price enters entry zone) → `HIT_TP` / `HIT_SL` / `EXPIRED` (14 days).
Resolved by `check_outcomes.py` via 4h candle walk. SL wins on same-candle ambiguity (conservative). MAE/MFE tracked per signal.

### Compatibility Note

`features/ta_calculator.py` patches `numpy.isnan` before importing pandas-ta for Python 3.13 + pandas 3.x compatibility. The patch is scoped and restored immediately after import.

## Project Status

**V4 is the active plan** (approved 2026-03-30). Multi-asset universe + AI-first architecture. See [ROADMAP.md](ROADMAP.md) for the full plan.

V3 is complete. V2 is complete.

**V4 Phase 1 (complete):** Multi-asset foundation — universe restructuring, YFinanceDataFetcher, class-leader gates, honest R:R, multi-source outcome checker. All 176 tests pass.

**V4 Phase 1.5 (next):** AI-first signal generation — replace QuantAgent + StrategyAgent with TradeAnalyst (LLM picks entry/SL/TP), replace SentimentAgent with MacroAnalyst, replace RiskAgent with RiskReviewer (portfolio-level LLM reasoning), delete mechanical gates, add Python validation layer.
