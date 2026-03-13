# v1 Risks & Constraints

> This document defines the hard boundaries of `crypto-swing-copilot` v1.
> Violating these constraints is a **shipping blocker**, not a backlog item.

---

## 1 – What v1 MUST NOT Do

| # | Prohibited Action | Reason |
|---|---|---|
| 1 | Place, amend, or cancel exchange orders | Tool is read-only research; no trading automation |
| 2 | Use leverage, margin, or futures products | Swing strategy is spot-only; leverage violates mandate |
| 3 | Trade intraday (< 4 h bar) | v1 timeframes are 4 h and 1 D only |
| 4 | Connect to any authenticated API endpoint | Avoids accidental order placement; reduces secret exposure |
| 5 | Execute trades programmatically in any form | Human executes; tool advises |
| 6 | Store exchange API keys with write permissions | Read-only keys only; write keys must never be used |
| 7 | Run live unsupervised (cron without human review) | Playbook must be reviewed before any trade |
| 8 | Optimise strategy parameters on historical data | Sanity checks only; no curve-fitting |
| 9 | Open more than `max_open_positions` concurrent trades | Enforced by `RiskAgent` and `risk_profile.json` |
| 10 | Risk more than `max_risk_per_trade` of capital per trade | Enforced by Python position-sizing math, not LLM |

---

## 2 – The LLM Math Trap

**Risk**: An LLM agent "helpfully" computes a position size, stop-loss price, or percentage return inside its response.

**Constraint**:

- **Python computes all numbers.** `ta_calculator.py` owns ATR, RSI, EMA, drawdown, position size, and every other quantitative value.
- **LLMs receive a `PairSnapshot`** containing pre-computed values in a typed Pydantic model.
- **Agent prompts explicitly instruct**: *"Do not perform arithmetic. Use only the values provided in the snapshot."*
- **Pydantic output schemas** for `QuantSignal`, `SentimentSignal`, `SetupProposal`, and `PlaybookEntry` contain no free-form numeric fields – only enums, labels, and quoted snapshot values.
- **Langfuse traces** are reviewed periodically to catch any LLM-computed numbers sneaking through.

---

## 3 – News & Sentiment Constraints

| Source | Allowed | Excluded |
|---|---|---|
| Fear & Greed Index (alternative.me) | ✅ Official API, numeric index only | — |
| CoinDesk RSS (1-2 feeds max) | ✅ Stable, text-only headlines | Full article body, paywalled content |
| Twitter / X | ❌ Rate limits, noise, unreliable | All social feeds |
| Crypto Twitter aggregators | ❌ Unverified, gameable | All |
| Reddit, Telegram, Discord | ❌ Noise, manipulation risk | All |
| On-chain analytics APIs | ❌ Out of scope for v1 | Glassnode, Nansen, etc. |
| TradingView webhooks | ❌ Out of scope for v1 | — |

**Why**: Stable, URL-stable, machine-readable sources only. Volatile or high-volume feeds introduce noise and API fragility.

---

## 4 – Backtesting Constraints

| Allowed | Not Allowed |
|---|---|
| Walk-forward spot-checks on 30–90 days of data | Grid search / parameter optimisation |
| Visual inspection of signal on historical chart | Forward-projection with back-tested params |
| Sanity check: "does RSI crossover precede price move?" | Sharpe-ratio chasing, hyper-parameter tuning |

**Why**: Any optimisation on the same dataset the system will trade against constitutes look-ahead bias. v1 validates signal logic, not profitability.

---

## 5 – Data Integrity Constraints

- **Parquet cache** stores raw OHLCV only. No derived features are persisted; they are recomputed on each run.
- **No data modification**: fetched candles are immutable once written. Re-fetch replaces the file.
- **Gap detection**: if OHLCV has missing bars, the run fails fast with a clear error rather than silently imputing.

---

## 6 – Observability Constraints

- Every LLM call MUST be wrapped in a Langfuse trace with: `pair`, `timeframe`, `agent_name`, prompt tokens, completion tokens, and wall-clock latency.
- No LLM call may bypass Langfuse (e.g., bare SDK calls without tracing).
- Langfuse project and API key are configured in `config/services.yaml`; they are **not** hard-coded.

---

## 7 – Dependency Constraints

| Allowed | Notes |
|---|---|
| `ccxt` | Binance spot, read-only |
| `pandas`, `pandas-ta` | All TA math |
| `pydantic` v2 | Schema validation and JSON serialisation |
| `langfuse` | LLM observability |
| `jinja2` | HTML report templating |
| `google-generativeai` or `langchain-google-genai` | Gemini Pro access |
| `feedparser` | RSS parsing |

Any new runtime dependency requires explicit justification and addition to this list before merging.
