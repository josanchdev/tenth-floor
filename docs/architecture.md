# System Architecture

## Data Flow

```mermaid
flowchart TD
    subgraph Sources["External Data Sources"]
        BIN["Binance Spot\nccxt · read-only · public endpoints"]
        FGI["Fear & Greed Index\nalternative.me/fng"]
        RSS["RSS Headlines\ncoindesk"]
    end

    subgraph Feature["Feature Engine  ·  pure Python"]
        MDF["MarketDataFetcher\ndata/market_data.py\nOHLCV fetch + Parquet cache"]
        SF["SentimentFetcher\ndata/sentiment.py\ngraceful degradation"]
        TAC["TACalculator\nfeatures/ta_calculator.py\npandas-ta · EMA/RSI/MACD/BB/ATR/OBV"]
        SB["SnapshotBuilder\nfeatures/pair_snapshot.py\nassembles PairSnapshot"]
    end

    subgraph Contracts["Typed Data Contracts  ·  models.py"]
        SS["SentimentSnapshot"]
        PS["PairSnapshot\nsymbol · timeframe · price\nindicators · sentiment · recent closes"]
    end

    subgraph Agents["Agent Layer  ·  Gemini 2.5 Flash  ·  Langfuse traced"]
        QA["QuantAgent\nTrend regime · signal labels\nconfidence score 0–1"]
        SA["SentimentAgent\nMacro bias · risk narrative"]
        STA["StrategyAgent  🔒\nLONG setup proposal\nentry / SL / TP computed by Python"]
        RA["RiskAgent\nConviction tier assignment\nSHORT rejection · confidence gate"]
    end

    subgraph Output["Output Layer"]
        PE["PlaybookEntry list\nmodels.py"]
        DB["SQLite Logger\ndata/playbook_history.db\n[Task 12 — pending]"]
        DW["Discord Webhook\none embed per daily run\n[Task 13 — pending]"]
        AD["Admin Dashboard\nStreamlit · signal history\n[Task 14 — pending]"]
    end

    BIN --> MDF
    FGI --> SF
    RSS --> SF

    MDF --> TAC
    TAC --> SB
    SF --> SS
    SS --> SB
    SS --> SA

    SB --> PS
    PS --> QA
    PS --> STA

    QA --> SA
    QA -- "QuantSignal\n(regime · confidence)" --> STA
    SA -- "SentimentSignal\n(bias · narrative)" --> STA

    STA -- "SetupProposal" --> RA
    QA -- "confidence float" --> RA

    RA --> PE

    PE --> DB
    PE --> DW
    DB --> AD
```

---

## Module Reference

### `src/crypto_swing_copilot/`

| File | Responsibility | Key exports |
|---|---|---|
| `config.py` | Central path resolver. Locates project root via `pyproject.toml`. Exportes `PROJECT_ROOT`, `CONFIG_DIR`, `DATA_DIR`. | `PROJECT_ROOT`, `CONFIG_DIR`, `DATA_DIR` |
| `data/models.py` | All Pydantic v2 contracts. Single source of truth for every inter-module boundary. Frozen models — no mutation after construction. | `PairSnapshot`, `PlaybookEntry`, `SetupProposal`, `QuantSignal`, `SentimentSignal`, `SentimentSnapshot`, `PlaybookVerdict`, `DailyPlaybook` |
| `data/market_data.py` | Fetches OHLCV from Binance via ccxt (public, no API key). Caches to Parquet under `data/raw/`. Incremental fetch — only new bars are requested. Symbol normalisation via `_normalise_symbol()`. | `MarketDataFetcher` |
| `data/sentiment.py` | Fetches Fear & Greed index (alternative.me) and RSS headlines (feedparser). Returns `SentimentSnapshot`. Both sources degrade gracefully on failure. | `SentimentFetcher` |
| `features/ta_calculator.py` | Computes EMA-20/50/200, RSI-14, MACD, Bollinger Bands, ATR-14, OBV, Volume-SMA-20 from a Pandas DataFrame using pandas-ta. Returns `TAIndicators`. | `TACalculator` |
| `features/pair_snapshot.py` | Assembles `PairSnapshot` from an OHLCV DataFrame + optional `SentimentSnapshot`. Calls `TACalculator` internally. Bridge between data/feature layer and agent layer. | `SnapshotBuilder` |
| `agents/base.py` | Shared agent utilities: `call_gemini()` (Langfuse-traced Gemini wrapper), `parse_json_response()`, `load_agent_config()`, `load_risk_profile()`. | `call_gemini`, `parse_json_response`, `load_agent_config`, `load_risk_profile` |
| `agents/quant_agent.py` | Classifies trend regime and identifies technical signals. Produces a `confidence` score (0–1) from indicator consensus. **LOCKED.** | `QuantAgent` |
| `agents/sentiment_agent.py` | Classifies macro sentiment bias and generates a risk narrative from `SentimentSnapshot`. **LOCKED.** | `SentimentAgent` |
| `agents/strategy_agent.py` | Proposes LONG trade setups. Price levels (entry zone, SL, TP) are computed by Python; LLM decides LONG or SKIP. Hardcoded SHORT rejection. **LOCKED.** | `StrategyAgent` |
| `agents/risk_agent.py` | Final gatekeeper. Rejects SHORTs, rejects confidence < 0.65, assigns conviction tier (`high`/`standard`), enriches reasoning via LLM. Takes `(SetupProposal, confidence)` tuples. | `RiskAgent` |

### `config/`

| File | Purpose |
|---|---|
| `universe.json` | 15 Binance USDT spot pairs to analyse each run |
| `risk_profile.json` | Conviction tiers, SL ATR multiplier, TP R:R ratio, confidence threshold |
| `models.yaml` | Gemini model name + temperature + max tokens per agent |
| `services.yaml` | CCXT settings, sentiment API URLs, Langfuse config, Discord + DB paths |
| `spot_only.json` | Spot-only enforcement flags (no leverage, no futures, no margin) |

### `db/`

| File | Purpose |
|---|---|
| `schema.sql` | SQLite DDL for the `signals` table. Applied at runtime via `CREATE TABLE IF NOT EXISTS`. Version-controlled; the `.db` file is git-ignored. |

---

## Key Design Rules

### Python owns all arithmetic

`pandas-ta` computes every indicator. LLMs receive pre-computed numbers and must quote them verbatim — they never recompute. This is enforced at the prompt level in every agent's `_SYSTEM_PROMPT`.

Price levels in `StrategyAgent` are computed by `_compute_price_levels()` before the LLM is called. The LLM receives them in the prompt and is instructed to use them exactly.

### `PairSnapshot` is the boundary object

Everything upstream of the agents produces or enriches a `PairSnapshot`. Everything downstream consumes it. No agent imports `market_data.py` or `ta_calculator.py`. No data module imports agents.

### Symbol normalisation happens once

`MarketDataFetcher._normalise_symbol()` converts any input format to `BTCUSDT` (no slash, uppercase). All downstream modules — agents, DB logger, Discord notifier — receive and store symbols in this format. This is a hard contract.

### Locked files

These three files must not be modified. Their LLM behaviour and output schemas are stable and downstream logic depends on them:

- `agents/quant_agent.py`
- `agents/sentiment_agent.py`
- `agents/strategy_agent.py`

---

## Pending Tasks

| Task | Description | Blocked by |
|---|---|---|
| Task 12 | SQLite signal logger — writes approved `PlaybookEntry` records to `data/playbook_history.db` | — |
| Task 13 | Discord webhook notifier — posts the daily consolidated embed | Task 12 (needs open-signal count from DB) |
| Task 14 | Admin Streamlit dashboard — signal history, outcome tracking | Task 12 |
| Orchestrator | `main.py` — wires the full pipeline end-to-end | Tasks 12–13 |
