# The Tenth Floor AI

> Glass-box quantitative crypto research desk — institutional-grade
> swing-trade signals for a paid Discord community.

Subscribers receive a daily signal embed with full mathematical and
agentic reasoning. They execute trades manually on their own accounts.
The system never touches money, never places orders, never manages a
budget.

---

## How It Works

The pipeline fetches Binance spot OHLCV data for 13 pairs on the daily
timeframe, computes technical indicators deterministically in Python,
then routes enriched snapshots through a four-agent LLM pipeline.
Approved signals (max 2/day) are published to Discord and logged to
SQLite. Signal resolutions (TP hit, SL hit, expired) are also posted
to Discord for full transparency.

```
Binance OHLCV  ──┐
                 ├──▶  Feature Engine  ──▶  Quant + Sentiment + Strategy + Risk  ──▶  Discord + SQLite
Fear & Greed   ──┘       (Python)                 (Qwen3 32B via vLLM)
```

**Hard constraints:** spot only · no leverage · no futures · no
auto-execution · LONG setups only

See [docs/architecture.md](docs/architecture.md) for the full system
diagram with Mermaid flowchart.

---

## Project Status

| Layer | Status |
|---|---|
| Data layer (market data, sentiment, models) | Complete |
| Feature engine (TA indicators, snapshot assembly) | Complete |
| Agent layer (Quant, Sentiment, Strategy, Risk) | Complete |
| LLM backend (local Qwen3 32B AWQ via vLLM) | Complete |
| SQLite signal logger + outcome checker | Complete |
| Discord webhook notifier | Complete |
| Daily orchestrator (`main.py`) | Complete |
| Admin dashboard (Streamlit) | Complete |
| Structure-based S/R price levels | Complete |
| Deterministic trend scoring | Complete |
| BTC relative strength filter | Complete |
| Discord outcome notifications | Complete |

**144 tests** across 9 files. All mocked — no network calls, no LLM
server required.

---

## Quick Start

### Requirements

- Python 3.11+ (3.13 supported)
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
cd crypto-swing-copilot
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
# Full universe (13 pairs, daily timeframe)
python -m crypto_swing_copilot.main

# Specific pairs
python -m crypto_swing_copilot.main BTCUSDT ETHUSDT

# Dry run (no DB writes, no Discord posts)
python -m crypto_swing_copilot.main --dry-run
```

### Run tests

```bash
pytest          # all 144 tests
pytest -v       # verbose output
pytest --cov    # with coverage report
```

See [docs/deployment.md](docs/deployment.md) for production setup with
cron, outcome checking, and monitoring.

---

## Signal Output

Each approved signal carries:

- **Pair** and timeframe (1d)
- **Entry zone** anchored to structural support/resistance
- **Stop-loss** below swing low with ATR buffer
- **Take-profit** at nearest resistance (R:R >= 2.0 enforced)
- **Reward:Risk ratio** (minimum 2.0)
- **Conviction tier** (`high` = 2% suggested risk, `standard` = 1%)
- **Strategy rationale** (LLM-generated, full reasoning)
- **Outcome notifications** — TP hit, SL hit, and expiry updates posted to Discord

Max 2 signals per day. Silence is the default — only the strongest
setups are published. See [docs/signals.md](docs/signals.md) for the
full specification.

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
crypto-swing-copilot/
├── config/
│   ├── universe.json          # 13 Binance spot pairs to analyse
│   ├── risk_profile.json      # Conviction tiers, SL/TP parameters
│   ├── models.yaml            # LLM provider + per-agent config
│   ├── services.yaml          # External service configuration
│   └── spot_only.json         # Spot-only enforcement rules
├── db/
│   └── schema.sql             # SQLite DDL (version-controlled)
├── docs/
│   ├── architecture.md        # System design + Mermaid diagram
│   ├── signals.md             # Signal output specification
│   └── deployment.md          # Production deployment guide
├── src/crypto_swing_copilot/
│   ├── main.py                # Daily pipeline orchestrator
│   ├── config.py              # Central path resolver
│   ├── data/
│   │   ├── market_data.py     # Binance OHLCV via ccxt + Parquet cache
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
│   └── check_outcomes.py      # Standalone TP/SL resolution
├── tests/                     # 144 unit tests, all mocked
├── VISION.md                  # Product brief and V2 pivot spec
├── HANDOFF.md                 # Developer handoff with full status
└── KNOWN_LIMITATIONS.md       # Documented deferred items
```

---

## License

MIT — for research and signal-provider use. Not financial advice.
