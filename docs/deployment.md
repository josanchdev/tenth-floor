# Deployment & Operations

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
| Network | Outbound HTTPS to Binance, alternative.me, CoinDesk RSS, Discord, Langfuse |

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
# Full universe (26 pairs, 1d timeframe)
python -m crypto_swing_copilot.main

# Subset of pairs
python -m crypto_swing_copilot.main BTCUSDT ETHUSDT SOLUSDT

# Dry run — prints signals without DB writes or Discord posts
python -m crypto_swing_copilot.main --dry-run

# Verbose logging
python -m crypto_swing_copilot.main --log-level DEBUG
```

Typical runtime: ~30 seconds for 1 pair, ~3 minutes for the full 26-
pair universe (depends on LLM throughput; LLM short-circuit skips most
pairs on bearish days).

### Cron Scheduling

Run the pipeline daily. Choose a time after the 1d candle close
(00:00 UTC) to ensure complete daily data:

```cron
# Run pipeline at 00:15 UTC daily
15 0 * * * cd /path/to/crypto-swing-copilot && /path/to/venv/bin/python -m crypto_swing_copilot.main >> logs/pipeline.log 2>&1

# Run outcome checker at 06:00 UTC daily
0 6 * * * cd /path/to/crypto-swing-copilot && /path/to/venv/bin/python -m crypto_swing_copilot.check_outcomes >> logs/outcomes.log 2>&1
```

---

## Outcome Checker

The outcome checker is a standalone process that resolves published
signals by walking 4h candles:

```bash
# Check all PENDING/OPEN signals
python -m crypto_swing_copilot.check_outcomes

# Preview without writing to DB
python -m crypto_swing_copilot.check_outcomes --dry-run
```

### Signal Lifecycle

```
PENDING ──▶ OPEN ──▶ HIT_TP  (target reached)
                 ──▶ HIT_SL  (stop hit)
                 ──▶ EXPIRED (14 days, no resolution)
```

- **PENDING → OPEN**: a 4h candle low touches the entry zone
- **SL-first rule**: if both TP and SL are hit on the same candle, SL
  is recorded (conservative assumption)
- **MAE/MFE**: tracked per signal during the candle walk
- **14-day expiry**: unresolved signals are marked EXPIRED

---

## Monitoring

### Langfuse

Every LLM call is auto-traced via the `langfuse.openai.OpenAI` wrapper.
Agent-level `@observe` decorators create parent spans. The Langfuse
dashboard shows:

- Token usage and cost per agent per run
- Latency breakdown (which agent is slowest)
- Full prompt/response content for debugging
- Trace tree linking all 6 LLM calls in a single pipeline run

Access at [cloud.langfuse.com](https://cloud.langfuse.com) or your
self-hosted instance.

### Pipeline Logs

The pipeline logs to stderr at the configured level (`--log-level`).
Key events:

| Log | Meaning |
|---|---|
| `Fetched N bars for SYMBOL/TF` | OHLCV retrieval succeeded |
| `Sentiment snapshot: F&G=N` | Sentiment data retrieved |
| `Built N snapshots` | Feature engineering complete |
| `APPROVED: SYMBOL TF (high/standard)` | Signal passed risk gating |
| `REJECTED: SYMBOL TF — reason` | Signal filtered out |
| `Logged N signals to SQLite` | Database persistence succeeded |
| `Discord embed posted` | Webhook delivery succeeded |
| `DISCORD_WEBHOOK_URL not set` | Webhook skipped (no-op) |

### SQLite Queries

The signal database is a standard SQLite file at
`data/playbook_history.db`. Useful queries:

```sql
-- Open signals
SELECT pair, timeframe, conviction, entry_low, entry_high,
       stop_loss, take_profit, status
FROM signals
WHERE status IN ('PENDING', 'OPEN')
ORDER BY created_at DESC;

-- Win rate by conviction tier (requires 30+ closed trades)
SELECT conviction,
       COUNT(*) AS total,
       SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) AS wins,
       ROUND(100.0 * SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_pct
FROM signals
WHERE status IN ('HIT_TP', 'HIT_SL', 'EXPIRED')
GROUP BY conviction;

-- Recent signals with outcomes
SELECT pair, timeframe, conviction, status,
       reward_risk, max_adverse_excursion, max_favorable_excursion,
       created_at, outcome_date
FROM signals
ORDER BY created_at DESC
LIMIT 20;
```

---

## Configuration Reference

All configuration lives in `config/`. Files are version-controlled and
contain no secrets.

### `universe.json`

List of Binance USDT spot pairs to analyse. Add or remove pairs here
to change the coverage. All symbols use the canonical `BTCUSDT` format
(no slash).

### `risk_profile.json`

Trading parameters applied by `StrategyAgent` and `RiskAgent`:

| Key | Default | Description |
|---|---|---|
| `timeframes` | `["1d"]` | Candle timeframe (daily only) |
| `min_setup_confidence` | `0.57` (validation) / `0.65` (production) | Minimum confidence to publish |
| `stop_loss_atr_multiplier` | `1.2` | SL = entry - (ATR × multiplier) |
| `take_profit_rr_ratio` | `2.0` | TP = entry + (SL distance × ratio) |
| `conviction_tiers.high` | `{min: 0.80, risk: 0.02}` | High conviction tier |
| `conviction_tiers.standard` | `{min: 0.57, risk: 0.01}` | Standard conviction tier (validation) |

### `models.yaml`

LLM provider and per-agent settings. The `defaults` section applies to
all agents; per-agent sections override specific fields:

```yaml
defaults:
  provider: openai
  base_url: http://localhost:8000/v1
  model: Qwen/Qwen3-32B-AWQ

quant_agent:
  temperature: 0.1
  max_output_tokens: 1024
```

To switch to a different model (e.g. Llama, Mistral, or a cloud API),
change `model` and `base_url` here. No code changes required.

### `services.yaml`

External service configuration: ccxt settings, sentiment API URLs,
Langfuse parameters, Discord rate limits, and database path. See the
file for the full structure.

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
pipeline run will re-fetch from Binance (up to 500 bars per pair).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused localhost:8000` | vLLM not running | Start the inference server |
| `Model not found` | Model ID mismatch | Check `models.yaml` matches `vllm serve` model name |
| `DISCORD_WEBHOOK_URL not set` | Missing env var | Set in `.env` (pipeline continues without posting) |
| `pandas_ta` import error | Python 3.13 compat | Handled automatically by `ta_calculator.py` patches |
| `Langfuse flush timeout` | Network issue | Check `LANGFUSE_HOST`; traces are buffered and retried |
| Signals always rejected | Low confidence / extreme sentiment | Check Langfuse traces for agent reasoning; expected in strong downtrends. See ROADMAP.md for pipeline diagnostics (V3). |
