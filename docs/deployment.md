# Deployment & Operations

> **Note:** This document describes the current deployment. Phase 1.5 (AI-first)
> will change the agent architecture (MacroAnalyst + TradeAnalyst + RiskReviewer
> replacing the current 4 agents), increase LLM calls per run (~17-27 vs ~14-20),
> and may adjust pipeline runtime. See [ROADMAP.md](../ROADMAP.md) Phase 1.5.

This guide covers running The Tenth Floor AI in production: inference
server setup, daily scheduling, outcome tracking, and monitoring.

---

## Infrastructure

### Minimum Hardware

| Component | Spec |
|---|---|
| GPU | NVIDIA RTX 3090 (24 GB VRAM) or equivalent |
| RAM | 32 GB system memory |
| Storage | 10 GB for Parquet cache + model weights |
| Network | Outbound HTTPS to Binance, Yahoo Finance, alternative.me, CoinDesk RSS, Discord, Langfuse |

The AWQ 4-bit quantisation of Qwen3 32B uses ~18 GB VRAM, fitting
within 24 GB with room for KV cache at 4096 context length.

### vLLM Inference Server

```bash
# Install vLLM
pip install vllm

# Start serving Qwen3 32B AWQ
vllm serve Qwen/Qwen3-32B-AWQ \
  --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.90
```

Verify the server is running:

```bash
curl http://localhost:8000/v1/models
```

For a systemd service:

```ini
# /etc/systemd/system/vllm.service
[Unit]
Description=vLLM inference server
After=network.target

[Service]
Type=simple
User=your-user
ExecStart=/path/to/venv/bin/vllm serve Qwen/Qwen3-32B-AWQ \
  --port 8000 --max-model-len 4096 --gpu-memory-utilization 0.90
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Daily Pipeline

### Manual Run

```bash
# Full universe (36 assets across crypto, equities, ETFs, commodities)
python -m tenth_floor.main

# Specific symbols
python -m tenth_floor.main BTCUSDT AAPL SPY

# Filter by asset class
python -m tenth_floor.main --asset-class crypto

# Dry run — prints signals without DB writes or Discord posts
python -m tenth_floor.main --dry-run

# Config profile
python -m tenth_floor.main --profile validation

# Verbose logging
python -m tenth_floor.main --log-level DEBUG
```

Typical runtime: ~4-6 minutes for the full 36-asset universe (depends
on LLM throughput and how many assets pass pre-screening).

### Operational Script

`run.sh` handles the full operational cycle: start vLLM, run pipeline,
check outcomes, backup DB.

```bash
./run.sh                         # full run
./run.sh --dry-run               # test run (no DB writes, no Discord)
./run.sh --profile validation    # validation profile
./run.sh --reset-db              # wipe and recreate DB from schema.sql
./run.sh --outcomes-only         # skip pipeline, just check outcomes
```

### Cron Scheduling

Run the pipeline daily. Choose a time after the 1d candle close
(00:00 UTC) to ensure complete daily data:

```cron
# Run pipeline at 00:15 UTC daily
15 0 * * * cd /path/to/the-tenth-floor && ./run.sh >> logs/pipeline.log 2>&1

# Run outcome checker at 06:00 UTC daily
0 6 * * * cd /path/to/the-tenth-floor && /path/to/venv/bin/python -m tenth_floor.check_outcomes >> logs/outcomes.log 2>&1
```

---

## Outcome Checker

The outcome checker resolves published signals by walking candles from
the appropriate data source (ccxt for crypto, yfinance for equities/ETFs/
commodities) with per-asset-class check timeframes:

```bash
# Check all PENDING/OPEN signals
python -m tenth_floor.check_outcomes

# Preview without writing to DB
python -m tenth_floor.check_outcomes --dry-run
```

### Signal Lifecycle

```
PENDING ──▶ OPEN ──▶ HIT_TP  (target reached)
                 ──▶ HIT_SL  (stop hit)
                 ──▶ EXPIRED (14 days crypto, 30 days equity)
```

- **PENDING → OPEN**: a candle low touches the entry zone
- **SL-first rule**: if both TP and SL are hit on the same candle, SL
  is recorded (conservative assumption)
- **MAE/MFE**: tracked per signal during the candle walk

---

## Monitoring

### Langfuse

Every LLM call is auto-traced via the `langfuse.openai.OpenAI` wrapper.
Agent-level `@observe` decorators create parent spans. The Langfuse
dashboard shows:

- Token usage and cost per agent per run
- Latency breakdown (which agent is slowest)
- Full prompt/response content for debugging
- Trace tree linking all LLM calls in a single pipeline run

Access at [cloud.langfuse.com](https://cloud.langfuse.com) or your
self-hosted instance.

### Pipeline Logs

The pipeline logs to stderr at the configured level (`--log-level`).
Key events:

| Log | Meaning |
|---|---|
| `Fetching N crypto symbols via ccxt` | Crypto OHLCV fetch starting |
| `Fetching N equity/ETF symbols via yfinance` | Equity OHLCV fetch starting |
| `Sentiment snapshot: F&G=N` | Sentiment data retrieved |
| `Built N snapshots` | Feature engineering complete |
| `APPROVED: SYMBOL TF (high/standard)` | Signal passed review |
| `REJECTED: SYMBOL TF — reason` | Signal filtered out |
| `Logged N signals to SQLite` | Database persistence succeeded |
| `Discord embed posted` | Webhook delivery succeeded |

### SQLite Queries

The signal database is at `data/playbook_history.db`:

```sql
-- Open signals
SELECT pair, timeframe, asset_class, conviction,
       entry_low, entry_high, stop_loss, take_profit, status
FROM signals
WHERE status IN ('PENDING', 'OPEN')
ORDER BY created_at DESC;

-- Win rate by conviction tier
SELECT conviction,
       COUNT(*) AS total,
       SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) AS wins,
       ROUND(100.0 * SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_pct
FROM signals
WHERE status IN ('HIT_TP', 'HIT_SL', 'EXPIRED')
GROUP BY conviction;

-- Win rate by asset class
SELECT asset_class,
       COUNT(*) AS total,
       SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) AS wins,
       ROUND(100.0 * SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_pct
FROM signals
WHERE status IN ('HIT_TP', 'HIT_SL', 'EXPIRED')
GROUP BY asset_class;
```

---

## Configuration Reference

All configuration lives in `config/`. Files are version-controlled and
contain no secrets.

### `universe.json`

36 assets across 4 classes: crypto (12), equity (15), ETF (6), commodity (3).
Each asset class defines its data source, class leader, check timeframe,
and entry type.

### `risk_profile.json`

Trading parameters:

| Key | Default | Description |
|---|---|---|
| `timeframes` | `["1d"]` | Candle timeframe (daily only) |
| `min_setup_confidence` | `0.57` (validation) / `0.65` (production) | Minimum confidence to publish |
| `conviction_tiers.high` | `{min: 0.80, risk: 0.02}` | High conviction tier |
| `conviction_tiers.standard` | `{min: 0.57, risk: 0.01}` | Standard conviction tier |

### `models.yaml`

LLM provider and per-agent settings:

```yaml
defaults:
  provider: openai
  base_url: http://localhost:8000/v1
  model: Qwen/Qwen3-32B-AWQ
```

To switch models, change `model` and `base_url`. No code changes required.

### `services.yaml`

External service configuration: ccxt settings, sentiment API URLs,
Langfuse parameters, Discord rate limits, and database path.

---

## Backup

### What to back up

| Path | Contents | Frequency |
|---|---|---|
| `data/playbook_history.db` | All signal history | Daily |
| `data/raw/` | Parquet OHLCV cache | Optional (re-fetchable) |
| `.env` | Secrets | On change |

### Database backup

```bash
sqlite3 data/playbook_history.db ".backup data/playbook_history.db.bak"
```

The Parquet cache is a performance optimisation. If lost, the next
pipeline run will re-fetch from Binance/Yahoo Finance.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused localhost:8000` | vLLM not running | Start the inference server |
| `Model not found` | Model ID mismatch | Check `models.yaml` matches `vllm serve` model name |
| `DISCORD_WEBHOOK_URL not set` | Missing env var | Set in `.env` (pipeline continues without posting) |
| `pandas_ta` import error | Python 3.13 compat | Handled automatically by `ta_calculator.py` patches |
| `Langfuse flush timeout` | Network issue | Check `LANGFUSE_HOST`; traces are buffered and retried |
| Signals always rejected | Low confidence / extreme sentiment | Check Langfuse traces for agent reasoning |
| `bad symbol` in outcome checker | Equity symbol routed to ccxt | Check universe.json data_source config |
