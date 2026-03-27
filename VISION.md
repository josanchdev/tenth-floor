# THE TENTH FLOOR AI — Product Vision

## What This Is

A glass-box quantitative crypto research desk. The system fetches
Binance spot OHLCV data, computes technical indicators deterministically
in Python, runs a multi-agent LLM pipeline (Quant, Sentiment, Strategy,
Risk), and publishes daily swing-trade signals to a paid Discord
community via webhook.

Every signal includes full mathematical and agentic reasoning.
Subscribers execute trades manually on their own accounts.
The system never touches money, never places orders, never manages
a budget.

## The Business Model

This is a commercial signal provider for a premium paid Discord.
Signals must be institutional-grade, fully explained, and reproducible.
The system's credibility is its product. Code quality, observability,
and correctness are non-negotiable.

## Design Principles

1. **Python owns all math.** pandas-ta computes RSI, ATR, EMA,
   Bollinger Bands. LLMs never do arithmetic.
2. **LLMs reason and rank.** Agents interpret pre-computed snapshots;
   they don't crunch numbers.
3. **Spot only, LONG only.** No leverage, no futures, no short
   proposals. Hardcoded rejection at StrategyAgent and RiskAgent.
4. **Glass box.** Every signal includes full reasoning. Every LLM call
   is traced in Langfuse.
5. **Silence is the default.** Max 2 signals per day in production.
   Only publish when the evidence is overwhelming.
6. **Deterministic gating.** 7-signal trend score and Python-computed
   price levels drive all accept/reject decisions. LLMs advise, Python
   decides.
7. **Graceful degradation.** Sentiment sources failing never crashes
   the pipeline. Missing data = safe defaults.
8. **No secrets in config.** All keys are environment variables; config
   files are safe to commit.
9. **Local-first inference.** No cloud API dependency. Qwen3-32B-AWQ
   via vLLM on consumer hardware (RTX 3090). Model switching is a
   config change.

## Hard Constraints

- **LONG only** — SHORT proposals force-converted to SKIP
- **Spot only** — no futures, no margin, no leverage
- **R:R >= 2.0** — enforced by RiskAgent, non-negotiable
- **1d timeframe only** — daily candles, no intraday noise
- **Max 2 signals/day** (production) — quality over quantity
- **Symbol format: `BTCUSDT`** — normalised once at MarketDataFetcher
- **Duplicate-safe** — `UNIQUE(pair, timeframe, report_date)` + `INSERT OR IGNORE`

## Version History

### V2 (complete)

Pivoted from a personal trading tool to a public signal provider:
- Removed EUR budget management and per-trade sizing
- Added SQLite signal logging, Discord webhook output, conviction tiers
- Built 4-agent LLM pipeline (Quant, Sentiment, Strategy, Risk)
- Local inference via Qwen3-32B-AWQ on vLLM
- Structure-based S/R price levels, deterministic trend scoring
- BTC relative strength filter, outcome notifications
- Admin Streamlit dashboard
- 144 tests, all mocked

### V3 (in progress)

Addresses three problems revealed during the first week of live runs:
1. **Blindness** — no structured data on why signals die at each gate
2. **Inability to iterate** — can't test threshold changes without weeks of waiting
3. **Fragility** — no retries, no failure alerts, no config safety

See [ROADMAP.md](ROADMAP.md) for the full implementation plan.

### V4 (planned)

Post-V3, with research into whether additional tools, models, or agents
would improve signal quality. Possible directions:
- Per-pair/sector sentiment differentiation
- Statistical correlation-based risk gating
- Fine-tuning with GRPO on signal outcome data (requires 1000+ signals)
- Agent memory via SQLite signal history context
