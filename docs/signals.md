# Signal Specification

This document defines exactly what a signal is, how it is produced, and how it is published.

---

## Conviction Tiers

`RiskAgent` maps the `QuantAgent` confidence score to a conviction tier. This tier determines the suggested risk per trade communicated to subscribers.

| Confidence | Tier | Suggested risk |
|---|---|---|
| ≥ 0.80 | `high` | 2% of portfolio |
| ≥ 0.65 | `standard` | 1% of portfolio |
| < min_setup_confidence | — | Signal dropped, not published |

> The gating confidence comes from Python's deterministic `trend_score`
> (7-signal indicator agreement, 0–1). LLM-generated confidence is
> fallback only when `trend_score` is unavailable.
>
> `min_setup_confidence` defaults to 0.65 (production).
> Validation mode uses 0.57. See `config/profiles/`.

---

## Signal Fields

Each approved signal is a `PlaybookEntry` (defined in `data/models.py`):

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | Trading pair, e.g. `BTCUSDT` |
| `timeframe` | `str` | Candle timeframe: `1d` (pipeline is daily only) |
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
entry_zone_high  = current_price + (ATR_14 × 0.25)   # small buffer above spot
entry_zone_low   = current_price − (ATR_14 × 0.25)   # small buffer below spot
entry_mid        = current_price
stop_loss        = nearest support below price − (ATR_14 × 0.15)
                   fallback: entry_mid − (ATR_14 × stop_loss_atr_multiplier)
take_profit      = nearest resistance above price + (ATR_14 × 0.15)
                   fallback: entry_mid + (actual_risk × take_profit_rr_ratio)
```

Entry is at/near current market price so subscribers can act immediately.
SL is placed below structural support, TP at nearest resistance. R:R is
computed from the actual entry price — weak setups where price is far from
support are naturally killed by the R:R >= 2.0 gate.

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
| `"R:R {x} below minimum {2.0} – skipping"` | `reward_risk_ratio < take_profit_rr_ratio` |
| `"Confidence {x} below minimum {min_setup_confidence} – skipping"` | `confidence < min_setup_confidence` |

---

## RSI Bullish Divergence

`TACalculator._detect_rsi_divergence()` scans the last 30 daily bars for
bullish divergence: price makes a lower low but RSI makes a higher low.

Detection algorithm:
1. Find swing lows in price (local minima with 5-bar window)
2. Find swing lows in RSI at the same bar indices
3. If the most recent price low is lower than a prior low, but the
   corresponding RSI low is higher — bullish divergence is confirmed

The result is stored as `TAIndicators.rsi_divergence: bool | None`.
It is `True` when divergence is detected, `None` when insufficient
swing lows exist in the lookback window, and `False` otherwise.

Used by: QuantAgent (reported as a signal), StrategyAgent (counted as a
STRONG TECHNICAL and CONFLUENCE FACTOR), Gate 1 capitulation bypass.

---

## Capitulation Bypass (Gate 1)

Gate 1 (trend regime) normally kills all pairs in `STRONG_DOWNTREND`.
The capitulation bypass allows a pair through when two conditions are met:

1. **F&G rising from extreme fear**: Fear & Greed index is < 25 AND
   current value is rising from its 7-day trough (delta >= 3 points)
2. **RSI bullish divergence** on the specific pair

When both conditions hold, the pair bypasses Gate 1 and proceeds to
the remaining gates. This targets capitulation bottoms — exactly when
contrarian trades have the most edge.

The bypass is deliberately conservative:
- F&G direction (rising) matters more than level alone
- RSI divergence must be present on the specific pair, not just market-wide
- The pair must still pass all subsequent gates (strategy, confidence, R:R)

Backtesting (90 days, Dec 2025 – Mar 2026) shows the bypass triggers
rarely: 1 pair-day bypassed despite 64/90 days in extreme fear and 35
days with F&G rising. This is expected — the co-occurrence of extreme
fear + RSI divergence on a strong-downtrend pair is structurally rare.

---

## Discord Embed Format

One consolidated embed per daily run. Never one embed per signal.

```
Title:   🔟 THE TENTH FLOOR — {YYYY-MM-DD}
Color:   0x00ff88  (green)  if any approved signals
         0x888888  (grey)   if no approved signals

Per approved signal (one per pair — deduped):
  Field: "{pair} {TF} · LONG · {CONVICTION TIER}"
  Value: "Entry: {entry_low} – {entry_high}"
         "Stop: {stop_loss}  |  Target: {take_profit}"
         "R:R: {reward_risk}  ·  Risk: {suggested_risk_pct × 100}%"
         "Rationale: {strategy_rationale}"

Footer: "Open signals in DB: {n}  |  Powered by The Tenth Floor AI"
```

Implemented in `notifications/discord_notifier.py`.

---

## SQLite Schema

Approved signals are persisted to `data/playbook_history.db` (git-
ignored). The DDL lives in `db/schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id                TEXT PRIMARY KEY,
    created_at               TEXT NOT NULL,
    entered_at               TEXT,              -- when PENDING → OPEN
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
    status                   TEXT NOT NULL DEFAULT 'PENDING',
    outcome_price            REAL,
    outcome_date             TEXT,
    max_adverse_excursion    REAL,
    max_favorable_excursion  REAL,
    langfuse_trace_id        TEXT,

    UNIQUE(pair, timeframe, report_date)
);
```

The `UNIQUE` constraint prevents duplicate signals if the pipeline
runs twice on the same day. `INSERT OR IGNORE` silently skips
duplicates.

### Signal Lifecycle

```
PENDING ──▶ OPEN ──▶ HIT_TP  (target reached)
                 ──▶ HIT_SL  (stop hit)
                 ──▶ EXPIRED (14 days, no resolution)
```

- **PENDING**: Signal published but price has not entered the entry
  zone yet.
- **OPEN**: A 4h candle low dipped into the entry zone — signal is
  active. `entered_at` timestamp is recorded.
- **HIT_TP / HIT_SL**: Candle high/low reached TP or SL
  (chronological order, first hit wins; same-candle ambiguity assumes
  SL first — conservative).
- **EXPIRED**: 14 calendar days with no TP or SL hit.

### MAE / MFE Tracking

During the candle walk, the outcome checker records:

- **MAE** (Max Adverse Excursion): lowest low since entry — measures
  worst drawdown experienced.
- **MFE** (Max Favourable Excursion): highest high since entry —
  measures best unrealised gain.

These are essential for calibration at V2.1 (30+ closed trades).

Outcome tracking runs via `check_outcomes.py`. See
[deployment.md](deployment.md) for scheduling.
