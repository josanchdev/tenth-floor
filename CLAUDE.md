# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Install

```bash
pip install -e ".[dev]"          # editable install with dev deps (pytest, ruff, mypy, black)
```

## Commands

```bash
# Tests
pytest                                             # all 144 tests (mocked, no LLM/network needed)
pytest tests/test_agents.py -v                     # single file
pytest tests/test_main.py::TestPipeline::test_name # single test
pytest -k "dedup" -v                               # by pattern

# Lint & type check
ruff check src/ tests/                 # lint (E, F, I, UP, B rules; line-length 100)
ruff check --fix src/                  # auto-fix
mypy src/crypto_swing_copilot/         # type check (Python 3.11 target)

# Run pipeline
python -m crypto_swing_copilot.main                    # full 14-pair universe
python -m crypto_swing_copilot.main BTCUSDT ETHUSDT    # specific pairs
python -m crypto_swing_copilot.main --dry-run           # no DB writes, no Discord post

# Outcome checker (resolves PENDING/OPEN signals against real candles)
python -m crypto_swing_copilot.check_outcomes
python -m crypto_swing_copilot.check_outcomes --dry-run

# Operational script (starts vLLM + runs pipeline + outcomes + DB backup)
./run.sh                    # full run
./run.sh --dry-run          # test run
./run.sh --reset-db         # wipe and recreate DB from schema.sql
./run.sh --outcomes-only    # skip pipeline, just check outcomes

# Dashboard
streamlit run src/crypto_swing_copilot/dashboard/app.py
```

## Architecture

**The Tenth Floor AI** is a multi-agent LLM pipeline that publishes daily crypto swing-trade signals to a paid Discord community. It runs locally with Qwen3-32B-AWQ on vLLM.

### Pipeline Flow (main.py → `run_pipeline()`)

```
Binance OHLCV (ccxt) ──→ TACalculator ──→ SnapshotBuilder ──→ PairSnapshot
Fear & Greed + RSS   ──────────────────────────────────────↗

PairSnapshot ──→ QuantAgent (trend + confidence)
              ──→ StrategyAgent (LONG proposal or SKIP, with entry/SL/TP)
SentimentSnapshot ──→ SentimentAgent (macro bias — runs once, shared)

All proposals ──→ RiskAgent (conviction tiers, gates) ──→ PlaybookEntry[]
                                                           │
                                         ┌─────────────────┴──────────────┐
                                    SignalLogger (SQLite)        DiscordNotifier (webhook)
```

### Hard Rules

- **Python owns all arithmetic.** TA indicators computed by pandas-ta. Entry zones, SL, TP computed in `StrategyAgent._compute_price_levels()`. LLMs interpret and rank — they never compute prices.
- **Spot only, LONG only.** SHORT proposals are force-converted to SKIP in StrategyAgent and rejected by RiskAgent. No futures, no margin, no leverage.
- **Symbol format: `BTCUSDT`** (no slash). Normalised once at `MarketDataFetcher`. Every downstream module uses this format.
- **1d timeframe only.** Pipeline analyses daily candles exclusively — no intraday noise.
- **Max 2 signals per day.** Option B philosophy: silence is the default, only publish when the evidence is overwhelming.
- **Structure-based price levels.** Entry zones anchored to swing lows (S/R), SL below structural support, TP at nearest resistance. ATR fallback when no S/R detected.
- **Deterministic trend scoring.** 7-signal indicator agreement score (0–1) replaces LLM confidence for gating and conviction tiers.
- **BTC relative strength filter.** In fear markets, alts underperforming BTC are skipped.
- **R:R >= 2.0 enforced.** RiskAgent Rule 3 rejects proposals below `take_profit_rr_ratio`.
- **Duplicate-safe re-runs.** `UNIQUE(pair, timeframe, report_date)` + `INSERT OR IGNORE`.

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
| `universe.json` | Trading pairs (13 Binance spot USDT pairs) |
| `risk_profile.json` | Conviction tiers, timeframes, SL/TP params, confidence threshold |
| `models.yaml` | LLM provider routing + per-agent temp/token settings |
| `services.yaml` | External service URLs and cache settings |

Path resolution: `config.py` walks up from CWD looking for `pyproject.toml`. Override with `CRYPTO_SWING_COPILOT_ROOT` env var.

### Typed Contracts

`data/models.py` defines every inter-module boundary as a frozen Pydantic v2 model. The layers are: Data (`OHLCVBar`, `SentimentSnapshot`) → Features (`TAIndicators`, `PairSnapshot`) → Agent outputs (`QuantSignal`, `SentimentSignal`, `SetupProposal`, `PlaybookEntry`). No free-form numeric fields in agent outputs.

### Signal Lifecycle

`PENDING` → `OPEN` (price enters entry zone) → `HIT_TP` / `HIT_SL` / `EXPIRED` (14 days).
Resolved by `check_outcomes.py` via 4h candle walk. SL wins on same-candle ambiguity (conservative). MAE/MFE tracked per signal.

### Compatibility Note

`features/ta_calculator.py` patches `numpy.isnan` before importing pandas-ta for Python 3.13 + pandas 3.x compatibility. The patch is scoped and restored immediately after import.
