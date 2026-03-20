# Signal Specification

This document defines exactly what a signal is, how it is produced, and how it is published.

---

## Conviction Tiers

`RiskAgent` maps the `QuantAgent` confidence score to a conviction tier. This tier determines the suggested risk per trade communicated to subscribers.

| Confidence | Tier | Suggested risk |
|---|---|---|
| ≥ 0.80 | `high` | 2% of portfolio |
| ≥ 0.65 | `standard` | 1% of portfolio |
| < 0.65 | — | Signal dropped, not published |

The confidence score is LLM-generated from indicator consensus. It is a policy input, not a statistically calibrated probability. See [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md#2-quantagent-confidence-score-is-not-statistically-calibrated).

---

## Signal Fields

Each approved signal is a `PlaybookEntry` (defined in `data/models.py`):

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | Trading pair, e.g. `BTCUSDT` |
| `timeframe` | `str` | Candle timeframe, e.g. `4h` or `1d` |
| `report_date` | `str` | ISO date of the daily run |
| `verdict` | `PlaybookVerdict` | `APPROVED` or `REJECTED` |
| `verdict_reasoning` | `str` | Why the signal was approved or rejected |
| `direction` | `SignalDirection` | Always `LONG` for approved signals |
| `action` | `SetupAction` | `BUY` for approved signals |
| `entry_zone_low` | `float` | Lower bound of entry zone |
| `entry_zone_high` | `float` | Upper bound of entry zone |
| `stop_loss` | `float` | Stop-loss price (ATR-based, computed by Python) |
| `take_profit` | `float` | Take-profit price (R:R-based, computed by Python) |
| `reward_risk_ratio` | `float` | Reward-to-risk ratio |
| `confidence_score` | `float` | QuantAgent confidence (0–1) |
| `conviction` | `str` | `high` or `standard` |
| `suggested_risk_pct` | `float` | Suggested portfolio risk, e.g. `0.02` = 2% |
| `strategy_rationale` | `str` | StrategyAgent reasoning |
| `rank` | `int` | Priority within the daily playbook (1 = highest) |

---

## Price Level Computation

Price levels are computed by `StrategyAgent._compute_price_levels()` before the LLM is called. The LLM receives them and must use them verbatim.

```
entry_zone_low   = price × 0.995          # ±0.5% around spot (see KNOWN_LIMITATIONS §1)
entry_zone_high  = price × 1.005
stop_loss        = entry_low − (ATR_14 × stop_loss_atr_multiplier)
take_profit      = entry_high + (SL_distance × take_profit_rr_ratio)
```

Parameters are set in `config/risk_profile.json`:

```json
"stop_loss_atr_multiplier": 1.2,
"take_profit_rr_ratio": 2.0
```

---

## Rejection Reasons

`RiskAgent` may reject a setup before it is published. Rejected entries are not sent to Discord or logged to SQLite (only approved signals are stored).

| Rejection reason | Condition |
|---|---|
| `"Spot only – no shorting"` | `direction == SHORT` |
| `"Strategy action is skip"` | `action == SKIP` |
| `"Strategy action is hold"` | `action == HOLD` |
| `"Confidence {x} below minimum {0.65} – skipping"` | `confidence < min_setup_confidence` |

---

## Discord Embed Format

One consolidated embed per daily run. Never one embed per signal.

```
Title:   🔟 THE TENTH FLOOR — {YYYY-MM-DD}
Color:   0x00ff88  (green)  if any approved signals
         0x888888  (grey)   if no approved signals

Per approved signal:
  Field: "{pair} · LONG · {CONVICTION TIER}"
  Value: "Entry: {entry_low} – {entry_high}"
         "Stop: {stop_loss}  |  Target: {take_profit}"
         "R:R: {reward_risk}  ·  Risk: {suggested_risk_pct × 100}%"
         "Rationale: {strategy_rationale}"

Footer: "Open signals in DB: {n}  |  Powered by The Tenth Floor AI"
```

Implemented in Task 13.

---

## SQLite Schema

Approved signals are persisted to `data/playbook_history.db` (git-ignored). The DDL lives in `db/schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id                TEXT PRIMARY KEY,
    created_at               TEXT NOT NULL,
    report_date              TEXT NOT NULL,
    pair                     TEXT NOT NULL,
    timeframe                TEXT NOT NULL,
    direction                TEXT NOT NULL,
    conviction               TEXT NOT NULL,
    confidence_score         REAL NOT NULL,
    entry_low                REAL NOT NULL,
    entry_high               REAL NOT NULL,
    stop_loss                REAL NOT NULL,
    take_profit              REAL NOT NULL,
    reward_risk              REAL NOT NULL,
    suggested_risk_pct       REAL NOT NULL,
    strategy_rationale       TEXT,
    status                   TEXT NOT NULL DEFAULT 'OPEN',
    outcome_price            REAL,
    outcome_date             TEXT,
    max_adverse_excursion    REAL,
    max_favorable_excursion  REAL,
    langfuse_trace_id        TEXT
);
```

`status` lifecycle: `OPEN` → `HIT_TP` | `HIT_SL` | `EXPIRED`. Outcome fields are updated manually or via the admin dashboard (Task 14).

Implemented in Task 12.
