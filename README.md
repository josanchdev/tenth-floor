# The Tenth Floor AI

> Glass-box quantitative crypto research desk — institutional-grade swing-trade signals for a paid Discord community.

Subscribers receive a daily signal embed with full mathematical and agentic reasoning. They execute trades manually on their own accounts. The system never touches money, never places orders, never manages a budget.

---

## What It Does

The pipeline fetches Binance spot OHLCV data for 15 pairs, computes technical indicators deterministically in Python, then routes enriched snapshots through a four-agent LLM pipeline. Approved signals are published to Discord and logged to SQLite.

```
Binance OHLCV  ──┐
                 ├─→  Feature Engine  →  Quant + Sentiment + Strategy + Risk  →  Discord + SQLite
Fear & Greed   ──┘        (Python)              (Qwen3 32B via vLLM)
```

**Hard constraints:** spot only · no leverage · no futures · no auto-execution · LONG setups only

---

## Project Status

| Layer | Status |
|---|---|
| Data layer (market data, sentiment, models) | **Complete** |
| Feature engine (TA indicators, snapshot assembly) | **Complete** |
| Agent layer (Quant, Sentiment, Strategy, Risk) | **Complete** |
| LLM backend (local inference via vLLM) | **Complete** |
| SQLite signal logger + outcome checker | Task 12 — pending |
| Discord webhook notifier | Task 13 — pending |
| Admin dashboard | Task 14 — pending |
| Daily orchestrator (`main.py`) | Pending (blocked on Tasks 12–13) |

See [docs/architecture.md](docs/architecture.md) for the full system diagram.

---

## Setup

### Requirements

- Python 3.11+
- Local LLM inference server (vLLM recommended) with Qwen3 32B or compatible model
- GPU with ≥ 32GB VRAM (RTX 5090 or equivalent)
- A Langfuse account (free tier works)
- A Discord webhook URL (for output — not required to run the pipeline locally)

### Install

```bash
git clone <repo>
cd crypto-swing-copilot
pip install -e ".[dev]"
```

### Start the inference server

```bash
vllm serve Qwen/Qwen3-32B --port 8000
```

### Environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse project secret key |
| `LANGFUSE_HOST` | No | Self-hosted Langfuse URL (default: cloud.langfuse.com) |
| `LLM_BASE_URL` | No | Override inference server URL (default: `http://localhost:8000/v1`) |
| `OPENAI_API_KEY` | No | Only if your inference server requires auth |
| `DISCORD_WEBHOOK_URL` | Task 13 | Discord webhook for signal publishing |

### Run tests

```bash
pytest
```

51 tests, no network calls, no LLM server required.

---

## Signal Output

Each approved signal carries:

- **Pair** and timeframe
- **Entry zone** (low / high)
- **Stop-loss** and **take-profit** (Python-computed from ATR)
- **Reward:Risk ratio**
- **Conviction tier** (`high` = 2% suggested risk, `standard` = 1%)
- **Strategy rationale** (LLM-generated, full reasoning)

Signals below 0.65 confidence are silently dropped. See [docs/signals.md](docs/signals.md) for the full spec.

---

## LLM Backend

The system uses a **local-first** inference architecture. `call_llm()` in `base.py` routes to any OpenAI-compatible API — no vendor lock-in.

| Setting | Value |
|---|---|
| Default model | Qwen3 32B |
| Inference server | vLLM (OpenAI-compatible) |
| Config file | `config/models.yaml` |

To switch models or providers, edit `models.yaml` only — no code changes required.

---

## Design Principles

1. **Python owns all math.** `pandas-ta` computes RSI, ATR, EMA, Bollinger Bands. LLMs never do arithmetic.
2. **LLMs reason and rank.** Agents interpret pre-computed snapshots; they don't crunch numbers.
3. **Spot only, LONG only.** No leverage, no futures, no short proposals. StrategyAgent is hardcoded to reject SHORT.
4. **Glass box.** Every signal includes full reasoning. Every LLM call is traced in Langfuse.
5. **Graceful degradation.** Sentiment sources failing never crashes the pipeline.
6. **No secrets in config.** All keys are environment variables; config files are safe to commit.
7. **Local-first inference.** No cloud API dependency. Model switching is a config change.

---

## Repository Layout

```
crypto-swing-copilot/
├── config/
│   ├── universe.json          # 15 Binance spot pairs to analyse
│   ├── risk_profile.json      # Conviction tiers, SL/TP parameters
│   ├── models.yaml            # LLM provider, model, temperature per agent
│   ├── services.yaml          # External service configuration
│   └── spot_only.json         # Spot-only enforcement rules
├── db/
│   └── schema.sql             # SQLite DDL (version-controlled)
├── docs/
│   ├── architecture.md        # System design and data-flow diagram
│   └── signals.md             # Signal output specification
├── src/crypto_swing_copilot/
│   ├── config.py              # Central path resolver
│   ├── data/
│   │   ├── market_data.py     # CCXT fetch + Parquet cache
│   │   ├── sentiment.py       # Fear & Greed + RSS
│   │   └── models.py          # Pydantic contracts for every layer
│   ├── features/
│   │   ├── ta_calculator.py   # pandas-ta indicator computation
│   │   └── pair_snapshot.py   # PairSnapshot assembly
│   └── agents/
│       ├── base.py            # Provider-agnostic LLM call, config loaders
│       ├── quant_agent.py     # Trend regime + confidence score
│       ├── sentiment_agent.py # Macro sentiment bias
│       ├── strategy_agent.py  # LONG setup proposal
│       └── risk_agent.py      # Conviction tier assignment + gating
├── tests/                     # 51 unit tests, all mocked
├── VISION.md                  # Product brief and V2 pivot spec
├── KNOWN_LIMITATIONS.md       # Documented deferred items
└── .env.example               # Required environment variables
```

---

## License

MIT — for research and signal-provider use. Not financial advice.
