# The Tenth Floor

> Personal multi-asset swing-trade research system. Not a product, not a
> service — a 12-month forward-testing experiment to see whether an LLM
> pipeline has actual edge.

Three specialist LLM agents (MacroAnalyst, TradeAnalyst, RiskReviewer)
analyse ~36 assets daily across crypto, US equities, ETFs, and
commodities. Python computes indicators and validates the LLM's chosen
entry/stop/target for sanity. Runs are triggered manually from a React
dashboard — no cron, no scheduler, no automated publishing. Every
approved signal is tracked to resolution so expectancy in R units is
measurable from real outcomes.

See [plan.md](plan.md) for the full experiment plan and decision gates.

---

## Pipeline

```
Data (ccxt / yfinance) ──→ TACalculator ──→ Indicators + Structural Levels
                                                    ↓
MacroAnalyst (1 LLM call) ──→ macro frame (regime, per-class impact)
                                                    ↓
Pre-screen (data quality only) ──→ ~30–36 candidates
                                                    ↓
TradeAnalyst (1 LLM call per candidate) ──→ BUY proposals with entry/SL/TP
                                                    ↓
Python validation (sanity checks) ──→ valid proposals
                                                    ↓
RiskReviewer (1 LLM call per proposal) ──→ approved signals + conviction
                                                    ↓
Signal cap (max 5 / run) ──→ SignalLogger + dashboard
```

**Hard constraints:** spot only · LONG only · no leverage · no futures ·
R:R ≥ 1.5 (business integrity floor) · 1d timeframe across all asset
classes.

---

## Quick Start

### Requirements

- Python 3.12+
- GPU with ≥ 24 GB VRAM (RTX 3090 with AWQ) or ≥ 32 GB (RTX 5090)
- [vLLM](https://docs.vllm.ai/) serving Qwen3-32B-AWQ or any
  OpenAI-compatible model
- Node 20+ (for the dashboard)
- A [Langfuse](https://langfuse.com) account for LLM tracing (free tier)

### Install

```bash
git clone <repo>
cd tenth-floor
pip install -e ".[dev]"
cd dashboard && npm install && cd ..
cp .env.example .env        # fill in LANGFUSE_* keys
```

### Run the dashboard

```bash
./dashboard.sh              # starts FastAPI (8765) + Vite (5173)
```

Open <http://localhost:5173>, start vLLM from the LLM status pill, then
click **Run pipeline**. Events stream live; approved signals land in
[data/playbook_history.db](data/playbook_history.db) and surface in the
Track Record view.

### CLI alternatives

```bash
./run.sh --local              # headless local pipeline (vLLM must already be running)
./run.sh --outcomes-only      # resolve PENDING/OPEN signals only
./run.sh --reset-db           # wipe + recreate the signal DB from schema.sql
pytest                        # all tests (mocked, no network)
```

### Environment

| Variable | Required | Description |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`  | yes | Langfuse project key |
| `LANGFUSE_SECRET_KEY`  | yes | Langfuse project secret |
| `LANGFUSE_HOST`        | no  | Self-hosted Langfuse URL (default: cloud) |
| `LLM_BASE_URL`         | no  | Override inference server URL (default: `http://localhost:8000/v1`) |
| `VLLM_MODEL`           | no  | Model id for the dashboard LLM launcher |
| `VLLM_PORT`            | no  | vLLM port (default 8000) |

See [.env.example](.env.example) for the full list and hardware profile
variants in [.env.3090](.env.3090) / [.env.5090](.env.5090).

---

## Repository Layout

```
tenth-floor/
├── config/
│   ├── universe.json           # 36-asset universe + sector mapping
│   ├── risk_profile.json       # Conviction tiers, R:R floor, max signals
│   ├── models.yaml             # LLM provider + per-agent config
│   ├── services.yaml           # External service configuration
│   └── profiles/               # validation / production overlays
├── db/
│   ├── schema.sql              # SQLite DDL (version-controlled)
│   └── migrations/             # Forward-only schema migrations
├── docs/
│   ├── architecture.md         # System design
│   └── signals.md              # Signal output specification
├── src/tenth_floor/
│   ├── main.py                 # Pipeline orchestrator
│   ├── check_outcomes.py       # TP/SL resolution against real candles
│   ├── events.py               # In-process event bus (feeds the dashboard WS)
│   ├── config.py               # Central path resolver
│   ├── universe.py             # Universe loader + asset queries
│   ├── validation.py           # Python sanity checks on LLM output
│   ├── data/                   # ccxt / yfinance / sentiment / typed models
│   ├── features/               # TA indicators + PairSnapshot assembly
│   ├── agents/                 # MacroAnalyst, TradeAnalyst, RiskReviewer
│   ├── db/signal_logger.py     # SQLite signal persistence
│   └── api/                    # FastAPI backend (signals, runs, llm, ws)
├── dashboard/                  # Vite + React 19 + Tailwind v4 frontend
├── tests/                      # Unit + integration tests (all mocked)
├── plan.md                     # 365-day experiment plan — source of truth
├── ROADMAP.md                  # V4 architecture history
├── run.sh                      # CLI helper (local / outcomes / reset-db)
├── dashboard.sh                # Dev launcher: uvicorn + Vite
└── docker-compose.yml          # vllm + api + dashboard stack
```

---

## Design Principles

1. **AI-first, Python validates.** The LLM decides BUY/SKIP and picks
   entry/SL/TP. Python computes indicators as context and validates the
   output for sanity — never overrides LLM judgement with mechanical
   rules.
2. **Minimal prompts.** Role + output format only. No prescriptive
   trading checklists, no fixed confluence lists. The LLM reasons freely.
3. **Dashboard-only trigger.** Runs happen when Jorge clicks a button.
   No cron, no scheduler.
4. **Glass box.** Every signal carries full LLM reasoning; every call
   is traced in Langfuse.
5. **Forward testing, not backtesting.** Cleaner data, no overfitting.
   The metric that matters is expectancy in R over the 12-month window.

---

## License

MIT — for personal research. Not financial advice.
