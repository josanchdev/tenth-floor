# Crypto Swing Copilot – System Architecture (v1)

## Overview

`crypto-swing-copilot` is a **manual swing-trading research tool**. It fetches OHLCV and
sentiment data for a curated universe of spot crypto pairs, computes technical features
deterministically in Python, then routes enriched context to specialised LLM agents that
produce a prioritised daily playbook. A human trader reviews and executes selectively.

> **Golden rule**: Python computes every number. LLMs only reason and summarise.

---

## Data-Flow Diagram

```mermaid
flowchart TD
    subgraph Sources["External Sources"]
        CCXT["ccxt / Binance REST\n(read-only, spot)"]
        FG["Fear & Greed API\nalternative.me"]
        RSS["CoinDesk RSS\n(1-2 feeds)"]
    end

    subgraph DataLayer["Data Layer  ·  src/data/"]
        Fetch["market_data.py\nOHLCV 4h / 1D → Parquet cache"]
        SentFetch["sentiment.py\nF&G index + headlines"]
    end

    subgraph Features["Feature Engine  ·  src/features/"]
        TA["ta_calculator.py\nEMA 20/50/200, RSI 14, MACD\nBollinger Bands, ATR 14, OBV"]
        SentScore["sentiment.scorer\nnumeric F&G score"]
        Snapshot["pair_snapshot.py\nPydantic PairSnapshot per pair"]
    end

    subgraph Agents["LLM Agents  ·  src/agents/  (Gemini Pro via Langfuse)"]
        Quant["QuantAgent\ntrend regime · signal list\nconfidence score"]
        Sentiment["SentimentAgent\nrisk narrative · bias label"]
        Strategy["StrategyAgent\nentry zone · SL · TP · rationale"]
        Risk["RiskAgent\nposition-size check\nmax-positions gate"]
    end

    subgraph Output["Output  ·  reports/YYYY-MM-DD/"]
        MD["playbook.md\nDaily Playbook (Markdown/HTML)"]
    end

    Langfuse[("Langfuse\nTrace & Eval store")]

    CCXT --> Fetch
    FG   --> SentFetch
    RSS  --> SentFetch
    Fetch     --> TA
    SentFetch --> SentScore
    TA        --> Snapshot
    SentScore --> Snapshot
    Snapshot  --> Quant
    Snapshot  --> Sentiment
    Quant     --> Strategy
    Sentiment --> Strategy
    Strategy  --> Risk
    Risk      --> MD
    Quant & Sentiment & Strategy & Risk --> Langfuse
```

---

## Module Breakdown

### 1 – Data Layer (`src/data/`)

| Module | Responsibility |
|---|---|
| `market_data.py` | Fetch OHLCV via `ccxt` (Binance, **read-only**). Persist to Parquet under `data/raw/`. Rate-limit aware. |
| `sentiment.py` | Poll Fear & Greed API; parse CoinDesk RSS headlines into plain text. |
| `models.py` | Pydantic models: `OHLCVBar`, `SentimentSnapshot`. |

**Constraints**: no authenticated endpoints, no order placement, no WebSocket streams in v1.

---

### 2 – Feature Engine (`src/features/`)

| Module | Responsibility |
|---|---|
| `ta_calculator.py` | Compute EMA 20/50/200, RSI 14, MACD, Bollinger Bands, ATR 14, OBV using `pandas-ta`. All arithmetic lives here. |
| `pair_snapshot.py` | Assemble `PairSnapshot` Pydantic model per pair – the single typed payload forwarded to every agent. |

**Constraints**: no LLM calls or agent imports inside feature modules; pure data transformation only.

---

### 3 – Agents (`src/agents/`)

Each agent receives a `PairSnapshot` (or aggregated list) and returns a validated Pydantic
response. Every call is traced via Langfuse with inputs, outputs, latency, and token cost.

| Agent | Input | Output Schema |
|---|---|---|
| `QuantAgent` | `PairSnapshot` | `QuantSignal` – trend regime, signal list, confidence |
| `SentimentAgent` | F&G score + headlines | `SentimentSignal` – risk narrative, bias label |
| `StrategyAgent` | `QuantSignal` + `SentimentSignal` | `SetupProposal` – entry zone, SL, TP, rationale |
| `RiskAgent` | `SetupProposal[]` + portfolio state | `PlaybookEntry[]` – filtered, position-sized |

**Constraints**: agents receive pre-computed numbers; they **MUST NOT** perform arithmetic.

---

### 4 – Report (`src/report/`)

| Module | Responsibility |
|---|---|
| `builder.py` | Renders `PlaybookEntry[]` to Markdown (+ optional HTML via Jinja2). |
| `exporter.py` | Writes dated file: `reports/YYYY-MM-DD/playbook.md`. |

---

### 5 – Orchestration (`src/orchestration/`)

| Module | Responsibility |
|---|---|
| `pipeline.py` | Wires all stages; called by the daily CLI entrypoint `run_daily.py`. |
| `run_daily.py` | CLI entrypoint: `python run_daily.py` → emits today's playbook. |

---

## Key Principles

1. **Deterministic math in Python** – `ccxt` + `pandas-ta` own every calculation. LLMs see snapshots, not raw candles.
2. **LLM for reasoning only** – Agents interpret, summarise, and rank. They do not compute RSI, ATR, or position size.
3. **Human-in-the-loop** – The output is a research playbook. Execution is 100% manual.
4. **Typed contracts** – Every inter-module boundary uses a Pydantic model with a JSON schema.
5. **Full observability** – Every LLM call is traced in Langfuse (prompt, response, latency, tokens, cost).
6. **Minimal external dependencies** – v1 uses only Binance spot REST, Fear & Greed API, and 1-2 RSS feeds.
7. **No state mutation** – The system never writes to an exchange. It is strictly read-and-report.
