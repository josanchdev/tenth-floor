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
mypy src/crypto_swing_copilot/         # type check (Python 3.11 target)

# Run pipeline
python -m crypto_swing_copilot.main                    # full 13-pair universe
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

# Tweet auto-poster
python -m crypto_swing_copilot.post_tweet                # draft + interactive post
python -m crypto_swing_copilot.post_tweet --draft-only   # generate draft only (no posting)
python -m crypto_swing_copilot.post_tweet 2026-03-28     # specific date

# Dashboard
streamlit run src/crypto_swing_copilot/dashboard/app.py
```

## Architecture

**The Tenth Floor AI** is a multi-agent LLM pipeline that publishes daily crypto swing-trade signals to a paid Discord community. It runs locally with Qwen3.5-27B on vLLM.

### Pipeline Flow (main.py → `run_pipeline()`)

```
Binance OHLCV (ccxt) ──→ TACalculator ──→ SnapshotBuilder ──→ PairSnapshot
Fear & Greed + RSS   ──────────────────────────────────────↗

PairSnapshot ──→ QuantAgent (trend + confidence)
              ──→ StrategyAgent (LONG proposal or SKIP, with entry/SL/TP)
SentimentSnapshot ──→ SentimentAgent (macro bias — runs once, shared)

All proposals ──→ 7 filtering gates ──→ RiskAgent (conviction tiers) ──→ PlaybookEntry[]
                                                                          │
                                                        ┌─────────────────┴──────────────┐
                                                   SignalLogger (SQLite)        DiscordNotifier (webhook)
```

### Filtering Gates (in order)

1. **Trend regime** — `STRONG_DOWNTREND` → skip pair entirely.
   *Exception:* capitulation bypass allows through when F&G is rising from
   extreme fear (< 25, rising from 7-day trough by ≥ 3 pts) AND RSI bullish
   divergence is detected on the pair.
2. **StrategyAgent** — LLM decides BUY or SKIP based on 2+ strong/weak technical signals
3. **Volume confirmation** — in downtrends, BUY requires volume >= 1.3× SMA-20
4. **BTC relative strength** — in fear markets, alts underperforming BTC → skip
5. **RiskAgent confidence** — below `min_setup_confidence` → rejected
6. **RiskAgent R:R** — below `take_profit_rr_ratio` (2.0) → rejected
7. **BTC correlation guard** — if BTC failed, cap alt signals to 2
8. **Sector diversity cap** — max 1 signal per sector (L1, L2, DeFi, meme, etc.)

Then: signal cap (max_daily_signals) and re-ranking by confidence.

**LLM short-circuit:** Pairs with `trend_score < min_confidence` are pre-filtered
before any LLM calls. Only qualifying pairs hit QuantAgent/StrategyAgent, saving
significant GPU time on bearish days.

### Hard Rules

- **Python owns all arithmetic.** TA indicators computed by pandas-ta. Entry zones, SL, TP computed in `StrategyAgent._compute_price_levels()`. LLMs interpret and rank — they never compute prices.
- **Spot only, LONG only.** SHORT proposals are force-converted to SKIP in StrategyAgent and rejected by RiskAgent. No futures, no margin, no leverage.
- **Symbol format: `BTCUSDT`** (no slash). Normalised once at `MarketDataFetcher`. Every downstream module uses this format.
- **1d timeframe only.** Pipeline analyses daily candles exclusively — no intraday noise.
- **Max 2 signals per day** (production). Option B philosophy: silence is the default, only publish when the evidence is overwhelming.
- **Market-price entry, structure-based SL/TP.** Entry zone at/near current price (subscribers can act immediately). SL below nearest structural support, TP at nearest resistance. R:R computed from actual entry — weak setups (price far from support) are naturally killed by the R:R gate.
- **Deterministic trend scoring.** 7-signal indicator agreement score (0–1) replaces LLM confidence for gating and conviction tiers. LLM confidence is fallback only.
- **BTC relative strength filter.** In fear markets, alts underperforming BTC are skipped.
- **R:R >= 2.0 enforced.** RiskAgent Rule 3 rejects proposals below `take_profit_rr_ratio`.
- **Capitulation bypass is conservative.** Requires both F&G rising from extreme fear AND RSI divergence on the specific pair. Direction matters more than level.
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
| `universe.json` | 26 Binance spot USDT pairs + sector mapping |
| `risk_profile.json` | Conviction tiers, SL/TP params, confidence threshold, max signals |
| `models.yaml` | LLM provider routing + per-agent temp/token settings |
| `services.yaml` | External service URLs and cache settings |

**Config profiles:** `config/profiles/production.json` (confidence 0.65, max 2 signals) and `config/profiles/validation.json` (confidence 0.57, max 3 signals). Switch via `--profile validation|production`. Base `risk_profile.json` holds production defaults; profiles overlay specific values.

Path resolution: `config.py` walks up from CWD looking for `pyproject.toml`. Override with `CRYPTO_SWING_COPILOT_ROOT` env var.

### Typed Contracts

`data/models.py` defines every inter-module boundary as a frozen Pydantic v2 model. The layers are: Data (`OHLCVBar`, `SentimentSnapshot`) → Features (`TAIndicators`, `PairSnapshot`) → Agent outputs (`QuantSignal`, `SentimentSignal`, `SetupProposal`, `PlaybookEntry`). No free-form numeric fields in agent outputs.

### Signal Lifecycle

`PENDING` → `OPEN` (price enters entry zone) → `HIT_TP` / `HIT_SL` / `EXPIRED` (14 days).
Resolved by `check_outcomes.py` via 4h candle walk. SL wins on same-candle ambiguity (conservative). MAE/MFE tracked per signal.

### Compatibility Note

`features/ta_calculator.py` patches `numpy.isnan` before importing pandas-ta for Python 3.13 + pandas 3.x compatibility. The patch is scoped and restored immediately after import.

## Project Status

V2 is complete. V3 Tier 1 is complete — see [ROADMAP.md](ROADMAP.md) for the full plan.
Tier 1 delivered: pipeline diagnostics/funnel, backtester, retry logic, stale prompt fixes, config profiles, failure alerting, capitulation bypass.
V3 Tier 1 also delivered: LLM short-circuit, 26-pair universe, sector diversity cap, DB migrations, CI, dynamic price precision, duplicate-safe Discord, market-price entries.
Remaining V3: Langfuse prompt management, richer sentiment, structured logging.
