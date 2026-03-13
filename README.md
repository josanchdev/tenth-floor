# Crypto Swing Copilot

> A **manual** crypto swing-trading research copilot — no auto-trading, no leverage, no surprises.

---

## What It Does

`crypto-swing-copilot` fetches OHLCV data for 10–15 spot pairs on Binance, computes technical
features deterministically in Python, then routes enriched context to specialised LLM agents
(Gemini Pro) that produce a prioritised **daily playbook** of swing-trade setups with full
reasoning. You review the playbook and execute trades yourself.

```
Market data  →  Feature engine  →  LLM agents  →  Daily playbook (Markdown)
  (ccxt)         (pandas-ta)         (Gemini)         (human reviews & acts)
```

**Non-goals (v1)**: auto-execution, leverage, futures, intraday signals, backtesting optimisation.

---

## Phase 0 Summary

Phase 0 establishes the project scaffold and design contracts:

| Deliverable | Path | Purpose |
|---|---|---|
| Architecture | `docs/architecture.md` | System overview, data-flow diagram, module breakdown |
| Constraints | `docs/v1_risks_and_constraints.md` | Hard limits, LLM math trap policy, data source rules |
| Universe | `config/universe.json` | 15 Binance spot pairs to analyse |
| Risk profile | `config/risk_profile.json` | Max positions, per-trade risk, timeframes |
| Models | `config/models.yaml` | LLM model + temperature per agent |
| Services | `config/services.yaml` | CCXT, sentiment APIs, Langfuse, report output |

No code runs yet — Phase 0 is design and contract definition.

---

## Project Structure

```
crypto-swing-copilot/
├── config/
│   ├── universe.json          # pairs to analyse
│   ├── risk_profile.json      # position & risk rules
│   ├── models.yaml            # LLM model assignments
│   └── services.yaml          # external service config
├── data/
│   └── raw/                   # Parquet OHLCV cache (git-ignored)
├── docs/
│   ├── architecture.md        # system design & data-flow
│   └── v1_risks_and_constraints.md
├── reports/                   # YYYY-MM-DD/playbook.md (git-ignored)
├── src/
│   ├── data/                  # market_data.py, sentiment.py, models.py
│   ├── features/              # ta_calculator.py, pair_snapshot.py
│   ├── agents/                # quant, sentiment, strategy, risk agents
│   ├── report/                # builder.py, exporter.py
│   └── orchestration/         # pipeline.py
├── run_daily.py               # CLI entrypoint
├── pyproject.toml             # dependencies
└── .env.example               # required environment variables
```

---

## How to Run the Daily Report

### Prerequisites

```bash
# 1. Install dependencies (Python 3.11+)
pip install -e ".[dev]"

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env: set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, GOOGLE_API_KEY
```

### Run

```bash
python run_daily.py
# Output: reports/YYYY-MM-DD/playbook.md
```

### Options

```bash
python run_daily.py --pairs BTCUSDT ETHUSDT   # override universe
python run_daily.py --timeframe 4h            # single timeframe
python run_daily.py --no-html                 # skip HTML render
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `GOOGLE_API_KEY` | Gemini Pro API key |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key |
| `LANGFUSE_HOST` | Langfuse host (default: cloud.langfuse.com) |

---

## Key Design Principles

1. **Python owns all math** — `pandas-ta` computes RSI, ATR, EMA. LLMs never do arithmetic.
2. **LLMs reason and rank** — agents interpret pre-computed snapshots; they don't crunch numbers.
3. **Human-in-the-loop** — the playbook is advice. You decide what to trade.
4. **Full observability** — every LLM call is traced in Langfuse.
5. **No secrets in config** — all keys are environment variables; configs are safe to commit.

---

## Next Steps (Phase 1)

- [ ] Implement `src/data/market_data.py` (CCXT fetch + Parquet cache)
- [ ] Implement `src/data/sentiment.py` (Fear & Greed + RSS)
- [ ] Implement `src/features/ta_calculator.py` (pandas-ta indicators)
- [ ] Implement `src/features/pair_snapshot.py` (Pydantic `PairSnapshot`)
- [ ] Implement four LLM agents with Pydantic output schemas
- [ ] Implement `src/report/builder.py` (Markdown + HTML)
- [ ] Wire `run_daily.py` pipeline
- [ ] Add Langfuse tracing to all agent calls
- [ ] Write integration tests with 30-day historical fixture data

---

## License

MIT — for personal/research use. Not financial advice.
