# Signal Specification

This document defines exactly what a signal is, how it is produced, and how it is published.

---

## Conviction Tiers

RiskReviewer assigns conviction tiers based on portfolio-level reasoning: setup quality, macro alignment, correlation with existing positions, and sector concentration.

| Confidence | Tier | Suggested risk |
|---|---|---|
| >= 0.80 | `high` | 2% of portfolio |
| >= 0.65 | `standard` | 1% of portfolio |
| < min_setup_confidence | — | Signal dropped, not published |

> `min_setup_confidence` defaults to 0.65 (production).
> Validation mode uses 0.57. See `config/profiles/`.

---

## Signal Fields

Each approved signal is a `PlaybookEntry` (defined in `data/models.py`):

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | Trading pair, e.g. `BTCUSDT`, `AAPL` |
| `timeframe` | `str` | Candle timeframe: `1d` (pipeline is daily only) |
| `report_date` | `str` | ISO date of the daily run |
| `verdict` | `PlaybookVerdict` | `APPROVED` or `REJECTED` |
| `verdict_reasoning` | `str` | RiskReviewer reasoning for the verdict |
| `direction` | `SignalDirection` | Always `LONG` for approved signals |
| `entry_zone_low` | `float` | Lower bound of entry zone |
| `entry_zone_high` | `float` | Upper bound of entry zone |
| `stop_loss` | `float` | Stop-loss price (LLM-chosen, structurally anchored) |
| `take_profit` | `float` | Take-profit price (LLM-chosen, structurally anchored) |
| `reward_risk_ratio` | `float` | Reward-to-risk ratio (Python-verified) |
| `confidence_score` | `float` | TradeAnalyst confidence (0-1) |
| `conviction` | `str` | `high` or `standard` (from RiskReviewer) |
| `suggested_risk_pct` | `float` | Suggested portfolio risk, e.g. `0.02` = 2% |
| `rationale` | `str` | TradeAnalyst trade thesis |
| `risk_notes` | `str` | RiskReviewer risk flags |
| `rank` | `int` | Priority within the daily playbook (1 = highest) |

---

## How Signals Are Produced

### AI-First Architecture

The LLM is the trader. Python is the risk manager.

1. **MacroAnalyst** (1 LLM call) reads VIX, Fear & Greed, DXY and outputs a macro regime (risk_on/risk_off/mixed/transitioning) with per-asset-class impact assessments.

2. **TradeAnalyst** (1 LLM call per candidate) receives full TA context + macro frame. Decides BUY or SKIP. If BUY: picks entry zone, SL, TP with structural reasoning anchored to support/resistance levels.

3. **Python validation** checks LLM output for sanity — not a judgment call, a safety net:
   - No SHORT (spot only, LONG only)
   - SL < entry < TP (basic directional sanity)
   - SL not > 15% below entry (prevents absurd stops)
   - TP not > 50% above entry (prevents fantasy targets)
   - R:R >= 1.5 hard floor (business integrity)
   - Entry zone low < entry zone high

4. **RiskReviewer** (1 LLM call, sees ALL proposals) reviews as a portfolio: correlation, sector concentration, macro alignment, conviction tiers. Approves the strongest, rejects the rest.

5. **Signal cap** limits output to max_daily_signals (default: 2 production, 3 validation).

### Rejection Reasons

| Source | Rejection reason |
|---|---|
| TradeAnalyst | Returns SKIP — no actionable setup |
| Python validation | SHORT direction, SL/TP sanity, R:R < 1.5, distance bounds |
| RiskReviewer | Correlated with another proposal, sector concentration, weak relative to alternatives, macro headwind |
| Signal cap | Approved but beyond daily limit — lowest confidence dropped |

---

## Discord Embed Format

One consolidated embed per daily run. Never one embed per signal.

```
Title:   THE TENTH FLOOR — {YYYY-MM-DD}
Color:   0x00ff88  (green)  if any approved signals
         0x888888  (grey)   if no approved signals

Per approved signal (one per pair — deduped):
  Field: "{pair} {TF} · LONG · {CONVICTION TIER}"
  Value: "Entry: {entry_low} – {entry_high}"
         "Stop: {stop_loss}  |  Target: {take_profit}"
         "R:R: {reward_risk}  ·  Risk: {suggested_risk_pct x 100}%"
         "{rationale}"

Footer: "Open signals in DB: {n}  |  Powered by The Tenth Floor AI"
```

Implemented in `notifications/discord_notifier.py`.

---

## SQLite Schema

Approved signals are persisted to `data/playbook_history.db` (git-ignored). The DDL lives in `db/schema.sql`.

The `UNIQUE(pair, timeframe, report_date)` constraint prevents duplicate signals if the pipeline runs twice on the same day. `INSERT OR IGNORE` silently skips duplicates.

### Signal Lifecycle

```
PENDING ──> OPEN ──> HIT_TP  (target reached)
                 ──> HIT_SL  (stop hit)
                 ──> EXPIRED (14 days, no resolution)
```

- **PENDING**: Signal published but price has not entered the entry zone yet.
- **OPEN**: A candle low dipped into the entry zone — signal is active. `entered_at` timestamp is recorded.
- **HIT_TP / HIT_SL**: Candle high/low reached TP or SL (chronological order, first hit wins; same-candle ambiguity assumes SL first — conservative).
- **EXPIRED**: 14 calendar days with no TP or SL hit.

### MAE / MFE Tracking

During the candle walk, the outcome checker records:

- **MAE** (Max Adverse Excursion): lowest low since entry — measures worst drawdown experienced.
- **MFE** (Max Favourable Excursion): highest high since entry — measures best unrealised gain.

Outcome tracking runs via `check_outcomes.py`. See [deployment.md](deployment.md) for scheduling.
