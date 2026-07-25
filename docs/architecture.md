# System Architecture

## Overview

The Tenth Floor AI is a multi-agent LLM pipeline that publishes daily swing-trade
signals across crypto, US equities, ETFs, and commodities. It runs locally with
Qwen3-32B-AWQ on vLLM.

**Core principle:** The LLM is the trader. Python is the risk manager.

---

## Pipeline Flow (Phase 1.5: AI-First)

3 reasoning agents + a Python validation layer. The LLM makes all trading
decisions (BUY/SKIP, entry/SL/TP). Python computes TA indicators as input
context, validates LLM output for sanity, and enforces hard business rules.

```
Data (ccxt/yfinance) ──→ TACalculator ──→ Indicators + Structural Levels
                                                   ↓
MacroAnalyst (1 LLM call) ──→ macro frame (regime, per-class impact)
                                                   ↓
Pre-screen (data-quality only) ──→ up to 20 candidates
                                                   ↓
TradeAnalyst (1 LLM call per candidate) ──→ BUY proposals with entry/SL/TP/reasoning
                                                   ↓
Python validation (sanity checks) ──→ valid proposals
                                                   ↓
RiskReviewer (1 LLM call per proposal) ──→ approved signals with conviction
                                                   ↓
Signal cap (business rule) ──→ featured signals ──→ SignalLogger + Discord
```

### Agents

| Agent | Calls | Role |
|-------|-------|------|
| **MacroAnalyst** | 1 per run | Reads VIX, F&G, DXY. Outputs macro regime + per-asset-class impact. Runs first — its output frames every TradeAnalyst call. |
| **TradeAnalyst** | 1 per candidate | Receives full TA context + macro frame. Decides BUY or SKIP. If BUY: picks entry zone, SL, TP with structural reasoning. |
| **RiskReviewer** | 1 per proposal | Reviews proposals one at a time, in macro-aware ranked order, carrying running portfolio state (already-approved signals + existing open signals) into each call: correlation, sector concentration, conviction tiers. |

### Python Validation Layer

Runs after TradeAnalyst, before RiskReviewer. Not a judgment call — a safety net.

| Check | Rule | Why |
|-------|------|-----|
| Direction | No SHORT | Spot only, LONG only |
| Price sanity | SL < entry < TP | Basic math |
| Stop distance | SL not > 15% below entry | Prevents absurd stops |
| Target distance | TP not > 50% above entry | Prevents fantasy targets |
| R:R verification | Recalculate from LLM's numbers, must be >= 1.5 | Business integrity |
| Entry zone | entry_zone_low < entry_zone_high | Basic sanity |

### Pre-screen

Extremely permissive — saves GPU time, not a quality filter.

Skip only when:
- Fewer than 10 recent closes
- Zero volume for 5+ consecutive days

Everything else goes to TradeAnalyst. The LLM decides quality, not Python.

---

## Module Reference

### `src/tenth_floor/`

| File | Responsibility |
|---|---|
| `main.py` | Daily pipeline orchestrator. CLI entry point. Langfuse traced. |
| `config.py` | Central path resolver. Locates project root via `pyproject.toml`. |
| `universe.py` | Loads `universe.json`. Asset queries: `symbols()`, `asset_class_for()`, `data_source_for()`, `class_leader_for()`, `sector_map()`. |
| `validation.py` | Python validation layer — sanity checks on LLM output. |
| `data/models.py` | Pydantic v2 contracts. Frozen models for every inter-module boundary. |
| `data/market_data.py` | Crypto OHLCV via ccxt. Incremental Parquet cache. |
| `data/yfinance_data.py` | Equity/ETF/commodity OHLCV via yfinance. Incremental Parquet cache. |
| `data/sentiment.py` | F&G Index + RSS headlines. Graceful degradation on failure. |
| `features/ta_calculator.py` | EMA-20/50/200, RSI-14, MACD, BB, ATR-14, OBV, Volume-SMA-20, support/resistance levels. |
| `features/pair_snapshot.py` | Assembles `PairSnapshot` from OHLCV + TA + sentiment. |
| `agents/base.py` | `call_llm()`, `parse_json_response()`, `clean_json_response()`, config loaders. |
| `agents/macro_analyst.py` | MacroAnalyst agent — macro regime + per-class impacts. |
| `agents/trade_analyst.py` | TradeAnalyst agent — per-asset BUY/SKIP with LLM-chosen levels. |
| `agents/risk_reviewer.py` | RiskReviewer agent — portfolio-level approval + conviction. |
| `db/signal_logger.py` | SQLite persistence. `INSERT OR IGNORE` duplicate safety. |
| `check_outcomes.py` | Signal resolution via candle walk. Routes to ccxt or yfinance per asset class. |
| `api/` | FastAPI backend — signals, runs, LLM lifecycle, WebSocket event stream. |
| `dashboard/` | Vite + React 19 + Tailwind v4 frontend (Track Record + Archive views). |

### `config/`

| File | Purpose |
|---|---|
| `universe.json` | 20 assets across 4 asset classes + sector mapping |
| `risk_profile.json` | Conviction tiers, R:R floor (1.5), confidence threshold, max signals |
| `models.yaml` | LLM provider, base URL, model name, per-agent temperature + max tokens |
| `services.yaml` | External service URLs, cache settings, DB path |
| `profiles/` | validation.json / production.json config overlays |

---

## LLM Backend

Provider-agnostic via `call_llm()` in `base.py`. Routes to any
OpenAI-compatible API. Default: **Qwen3 32B AWQ** via **vLLM** locally.

```yaml
# config/models.yaml
defaults:
  provider: openai
  base_url: http://localhost:8000/v1
  model: Qwen/Qwen3-32B-AWQ
```

The `LLM_BASE_URL` env var overrides `base_url` for deployment
flexibility. `clean_json_response()` handles Qwen3-specific artifacts
(`<think>` blocks) and markdown code fences before JSON parsing.

---

## Key Design Rules

### AI-first, Python validates

LLM agents make all trading decisions. Python computes indicators as
input context, validates LLM output for sanity (SL < entry < TP, R:R
math, price bounds), and enforces hard business rules (signal cap,
duplicate check, R:R floor).

### Minimal prompts

Agent prompts define role + context + output format. No prescriptive
trading rules, no "when to skip" checklists, no fixed factor lists.
The LLM reasons freely using its domain knowledge. This avoids prompt
over-specification (which caused 34/36 skips in early testing) and
scales naturally as models improve.

### `PairSnapshot` is the boundary object

Everything upstream of the agents produces or enriches a `PairSnapshot`.
Everything downstream consumes it. No agent imports `market_data.py` or
`ta_calculator.py`. No data module imports agents.

### Symbol normalisation happens once

Crypto: `BTCUSDT` (no slash, uppercase) via `MarketDataFetcher._normalise_symbol()`.
Equities/ETFs: standard tickers (`AAPL`, `SPY`, `GLD`).
LLM symbol output is overridden with the authoritative snapshot symbol.

### Immutable typed contracts

All Pydantic models use `frozen=True`. No mutation after construction.

### Graceful degradation

Every external dependency has a fallback path. Sentiment API down →
neutral defaults. Symbol fetch fails → log and skip. Discord webhook
unset → warn and continue.
