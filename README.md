# The Tenth Floor AI

> Multi-asset AI swing-trade analysis — institutional-grade signals
> across crypto, US equities, ETFs, and commodities for a paid Discord
> community.

Subscribers receive daily signal embeds with full mathematical and
agentic reasoning. They execute trades manually on their own accounts.
The system never touches money, never places orders, never manages a
budget.

---

## How It Works

The pipeline fetches daily OHLCV data, computes technical indicators
deterministically in Python, then routes enriched snapshots through 7
sequential filtering gates and a four-agent LLM pipeline. Approved
signals (max 2-3/day) are published to Discord and logged to SQLite.
Signal resolutions (TP hit, SL hit, expired) are also posted for full
transparency.

```
Data sources     ──┐
(ccxt, yfinance)   ├──▶  Feature Engine  ──▶  Quant + Sentiment + Strategy + Risk  ──▶  Discord + SQLite
Sentiment feeds  ──┘       (Python)                 (Qwen3 32B via vLLM)
```

**Hard constraints:** spot only · no leverage · no futures · no
auto-execution · LONG setups only

See [docs/architecture.md](docs/architecture.md) for the full system
diagram with Mermaid flowchart.

---

## Project Status

### V4 (active — approved 2026-03-30)

Multi-asset universe expansion. The core problem: 26 crypto pairs are
one correlated market — when BTC enters a downtrend, all pairs fail the
gates simultaneously. V4 expands to ~36 structurally uncorrelated assets
so the pipeline produces signals in any market regime.

See [ROADMAP.md](ROADMAP.md) for the full V4 plan.

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 — Foundation | Package rename, universe restructure, YFinanceDataFetcher | In progress |
| Phase 2 — Sentiment & Macro | MacroAgent, VIX/DXY/yields, RSS expansion | Planned |
| Phase 3 — Gate generalization | Market-leader gates, two-pass scheduling, conditional entries | Planned |
| Phase 4 — Validation | 90-day backtest, equity validation mode, MacroAgent review | Planned |
| Phase 5 — Launch | Multi-asset Discord channels, tweet drafter, dashboard updates | Planned |

### V3 (complete — 2026-03-27)

Pipeline diagnostics, backtester, LLM retry, config profiles, failure
alerting, LLM short-circuit, 26-pair universe, sector diversity cap,
RSI divergence + capitulation bypass, DB migrations, CI, dynamic price
precision, duplicate-safe Discord, market-price entries.

### V2 (complete)

Full pipeline: data layer, feature engine, 4-agent LLM pipeline, SQLite
logging, outcome checker, Discord notifier, admin dashboard, deterministic
trend scoring, BTC relative strength filter.

**176 tests** across 11 files. All mocked — no network calls, no LLM
server required.

---

## Quick Start

### Requirements

- Python 3.12+ (3.13 supported)
- Local LLM inference server ([vLLM](https://docs.vllm.ai/)
  recommended) serving Qwen3 32B AWQ or any OpenAI-compatible model
- GPU with >= 24 GB VRAM (RTX 3090 with AWQ quantisation, or
  >= 32 GB for full-precision)
- A [Langfuse](https://langfuse.com) account (free tier works)
- A Discord webhook URL (for output — not required for local
  development)

### Install

```bash
git clone <repo>
cd the-tenth-floor
pip install -e ".[dev]"
```

### Start the inference server

```bash
vllm serve Qwen/Qwen3-32B-AWQ \
  --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.90
```

### Configure environment

```bash
cp .env.example .env
# Edit .env — fill in Langfuse keys and Discord webhook URL
```

| Variable | Required | Description |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse project secret key |
| `LANGFUSE_HOST` | No | Self-hosted Langfuse URL (default: cloud) |
| `LLM_BASE_URL` | No | Override inference server URL (default: `http://localhost:8000/v1`) |
| `OPENAI_API_KEY` | No | Only if your inference server requires auth |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook for signal publishing |

### Run the pipeline

```bash
# Full universe
python -m tenth_floor.main

# Specific pairs
python -m tenth_floor.main BTCUSDT ETHUSDT

# Dry run (no DB writes, no Discord posts)
python -m tenth_floor.main --dry-run
```

### Run tests

```bash
pytest          # all 176 tests
pytest -v       # verbose output
pytest --cov    # with coverage report
```

See [docs/deployment.md](docs/deployment.md) for production setup with
cron, outcome checking, and monitoring.

---

## Signal Output

Each approved signal carries:

- **Symbol** and timeframe (1d)
- **Entry zone** at/near current market price
- **Stop-loss** below swing low with ATR buffer
- **Take-profit** at nearest resistance (R:R >= 2.0 enforced)
- **Reward:Risk ratio** (minimum 2.0)
- **Conviction tier** (`high` = 2% suggested risk, `standard` = 1%)
- **Strategy rationale** (LLM-generated, full reasoning)
- **Outcome notifications** — TP hit, SL hit, and expiry updates posted to Discord

Max 2 signals per day (production). Silence is the default — only the
strongest setups are published. See [docs/signals.md](docs/signals.md)
for the full specification.

---

## Design Principles

1. **Python owns all math.** `pandas-ta` computes RSI, ATR, EMA,
   Bollinger Bands. LLMs never do arithmetic.
2. **LLMs reason and rank.** Agents interpret pre-computed snapshots;
   they don't crunch numbers.
3. **Spot only, LONG only.** No leverage, no futures, no short
   proposals. Hardcoded rejection at StrategyAgent and RiskAgent.
4. **Glass box.** Every signal includes full reasoning. Every LLM call
   is traced in Langfuse.
5. **Graceful degradation.** Sentiment sources failing never crashes
   the pipeline.
6. **No secrets in config.** All keys are environment variables; config
   files are safe to commit.
7. **Local-first inference.** No cloud API dependency. Model switching
   is a config change.

---

## Repository Layout

```
the-tenth-floor/
├── config/
│   ├── universe.json          # Asset universe + sector mapping
│   ├── risk_profile.json      # Conviction tiers, SL/TP parameters
│   ├── models.yaml            # LLM provider + per-agent config
│   ├── services.yaml          # External service configuration
│   └── profiles/              # validation.json / production.json overlays
├── db/
│   └── schema.sql             # SQLite DDL (version-controlled)
├── docs/
│   ├── architecture.md        # System design + Mermaid diagram
│   ├── signals.md             # Signal output specification
│   └── deployment.md          # Production deployment guide
├── src/tenth_floor/
│   ├── main.py                # Daily pipeline orchestrator
│   ├── config.py              # Central path resolver
│   ├── backtest.py            # Historical replay / backtester
│   ├── check_outcomes.py      # Standalone TP/SL resolution
│   ├── post_tweet.py          # X/Twitter auto-poster
│   ├── data/
│   │   ├── market_data.py     # OHLCV via ccxt + Parquet cache
│   │   ├── sentiment.py       # Fear & Greed Index + RSS headlines
│   │   └── models.py          # Pydantic v2 contracts for every layer
│   ├── features/
│   │   ├── ta_calculator.py   # pandas-ta indicator computation
│   │   └── pair_snapshot.py   # PairSnapshot assembly
│   ├── agents/
│   │   ├── base.py            # Provider-agnostic LLM call + config
│   │   ├── quant_agent.py     # Trend regime + confidence score
│   │   ├── sentiment_agent.py # Macro sentiment bias
│   │   ├── strategy_agent.py  # LONG setup proposal
│   │   └── risk_agent.py      # Conviction tier + final gating
│   ├── db/
│   │   └── signal_logger.py   # SQLite signal persistence
│   ├── notifications/
│   │   └── discord_notifier.py # Discord webhook poster
│   ├── social/
│   │   ├── tweet_drafter.py   # LLM-powered tweet drafting
│   │   ├── tweet_poster.py    # X/Twitter API posting
│   │   └── discord_draft.py   # Tweet drafts to Discord
│   └── dashboard/
│       ├── app.py             # Streamlit admin dashboard
│       └── queries.py         # SQL + pandas queries for dashboard
├── tests/                     # 176 unit tests, all mocked
├── ROADMAP.md                 # V4 implementation plan
└── GTM.md                     # Go-to-market plan
```

---

## License

MIT — for research and signal-provider use. Not financial advice.
