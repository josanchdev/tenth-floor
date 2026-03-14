# Crypto Swing Copilot – Daily Workflow Guide

> **Audience**: Weekly / swing trader with a €100 portfolio on Binance **spot** (LONG only).
> **Goal**: Run the tool once a day, read the playbook, execute (or skip), log your trades.
> **⚠️ SPOT ONLY**: This system proposes LONG positions only. No shorting, no futures, no margin.

---

## 1. Daily Routine (5–10 minutes)

```
Step 1 → Run the pipeline
Step 2 → Read the playbook
Step 3 → Execute (or skip)
Step 4 → Update positions.json
```

### Step 1 – Run the pipeline

```bash
cd crypto-swing-copilot
python run_daily.py          # generates today's playbook
```

Output lands in `reports/YYYY-MM-DD/playbook.md`.

### Step 2 – Read the playbook

Open the generated playbook. You'll see a table like this:

```
┌─────────────────────────────────────────────────────────────────────┐
│                 DAILY PLAYBOOK – 2026-03-14                        │
├──────────┬────────┬──────────┬──────────┬──────────┬───────────────┤
│ Pair     │ Bias   │ Entry    │ SL       │ TP       │ Size (EUR)    │
├──────────┼────────┼──────────┼──────────┼──────────┼───────────────┤
│ BTC/USDT │ LONG   │ 62,100   │ 60,800   │ 64,700   │ 33.30         │
│ ETH/USDT │ LONG   │ 3,380    │ 3,280    │ 3,580    │ 33.30         │
│ SOL/USDT │ LONG   │ 148.50   │ 141.20   │ 163.10   │ 33.30         │
├──────────┴────────┴──────────┴──────────┴──────────┴───────────────┤
│ Confidence: BTC 0.82 · ETH 0.71 · SOL 0.67                        │
│ Rationale: BTC reclaimed 50 EMA on 4h, RSI 58 → room to run ...   │
└─────────────────────────────────────────────────────────────────────┘
```

Each row is a **setup proposal**. You decide what to trade.

### Step 3 – Execute (or skip)

- Open Binance → place a **spot limit order** at the entry price.
- Set your **stop-loss** and **take-profit** as shown.
- If you don't like a setup → skip it. The tool never forces you.

### Step 4 – Update `positions.json`

Log what you actually did. See [Section 2](#2-positionsjson-format) for the format.

---

## 2. `positions.json` Format

This file lives at the project root and tracks your real portfolio state.
A template is available at `positions_template.json`.

### Empty portfolio (starting state)

```json
{
  "portfolio_value_eur": 100.00,
  "cash_eur": 100.00,
  "open_positions": [],
  "closed_trades": [],
  "last_updated": "2026-03-14T08:00:00Z"
}
```

### Open position example

```json
{
  "portfolio_value_eur": 100.00,
  "cash_eur": 33.37,
  "open_positions": [
    {
      "pair": "BTC/USDT",
      "side": "LONG",
      "entry_price": 62100.00,
      "entry_date": "2026-03-14T09:15:00Z",
      "size_eur": 33.30,
      "quantity": 0.000536,
      "stop_loss": 60800.00,
      "take_profit": 64700.00,
      "fees_eur": 0.03
    },
    {
      "pair": "ETH/USDT",
      "side": "LONG",
      "entry_price": 3380.00,
      "entry_date": "2026-03-14T09:20:00Z",
      "size_eur": 33.30,
      "quantity": 0.00985,
      "stop_loss": 3280.00,
      "take_profit": 3580.00,
      "fees_eur": 0.03
    }
  ],
  "closed_trades": [],
  "last_updated": "2026-03-14T09:25:00Z"
}
```

### Closed trade example (TP hit)

```json
{
  "pair": "BTC/USDT",
  "side": "LONG",
  "entry_price": 62100.00,
  "entry_date": "2026-03-14T09:15:00Z",
  "exit_price": 64700.00,
  "exit_date": "2026-03-17T14:30:00Z",
  "size_eur": 33.30,
  "quantity": 0.000536,
  "pnl_eur": 1.39,
  "pnl_pct": 4.19,
  "exit_reason": "TP_HIT",
  "fees_eur": 0.07
}
```

---

## 3. When & How to Update `positions.json`

| Event | What to do |
|---|---|
| **New entry** | Add object to `open_positions[]`, subtract `size_eur` from `cash_eur` |
| **TP hit** | Move from `open_positions[]` → `closed_trades[]`, set `exit_reason: "TP_HIT"`, add proceeds back to `cash_eur` |
| **SL hit** | Same as TP but `exit_reason: "SL_HIT"`, adjust `cash_eur` for the loss |
| **Manual close** | Same flow, `exit_reason: "MANUAL"` — add a note in the rationale if you like |
| **End of day** | Recalculate `portfolio_value_eur` = `cash_eur` + sum of open position mark-to-market values |
| **Always** | Update `last_updated` to current UTC timestamp |

### PnL calculation (LONG only — spot trading)

```
pnl_eur   = (exit_price - entry_price) × quantity
pnl_pct   = (pnl_eur / size_eur) × 100
fees_eur   = entry_fees + exit_fees
net_pnl   = pnl_eur - fees_eur
```

---

## 4. Fee-Aware Sizing

The system sizes positions to **use ~100% of your cash** across the maximum number of open positions (default: 3). This means each position gets roughly **€33.30** on a €100 portfolio.

### Why not smaller trades?

Binance charges **0.1% per trade** (taker fee). On a €10 trade, that's €0.02 in + €0.02 out = **€0.04 round-trip**. You'd need a 0.4% move just to break even. On a small portfolio, trades below €20 are not worth the fees.

### How sizing works

```
available_per_trade = cash_eur / max_open_positions
                    = 100 / 3
                    = €33.33

fee_per_side        = available_per_trade × trading_fees_pct
                    = 33.33 × 0.001
                    = €0.03

net_position        = available_per_trade - fee_per_side
                    = €33.30
```

### Config keys in `risk_profile.json`

| Key | Default | Meaning |
|---|---|---|
| `target_cash_utilization` | `1.0` | Spend 100% of cash across `max_open_positions` |
| `min_position_eur` | `20` | Ignore any setup where position would be < €20 |
| `trading_fees_pct` | `0.001` | Binance spot taker fee (0.1%) |

---

## 5. What the Playbook Looks Like

When you open `reports/YYYY-MM-DD/playbook.md`, expect something like:

```markdown
# 📊 Daily Playbook – 2026-03-14

## Market Context
- Fear & Greed: 62 (Greed)
- BTC trend: Bullish (above 50 EMA on 4h & 1D)
- Key headlines: "ETF inflows hit $500M", "Fed holds rates steady"

---

## Setups

### 1. BTC/USDT — LONG ✅ (confidence: 0.82)
| Field       | Value      |
|-------------|------------|
| Entry zone  | 62,000–62,200 |
| Stop-loss   | 60,800 (ATR-based) |
| Take-profit | 64,700 (2.0 R:R) |
| Position    | €33.30 (0.000536 BTC) |

**Rationale**: Price reclaimed 50 EMA on 4h with increasing OBV.
RSI 58 — room to run before overbought. F&G supports risk-on.

### 2. ETH/USDT — LONG (confidence: 0.71)
...

### 3. SOL/USDT — LONG (confidence: 0.67)
...

---

## Risk Summary
- Open positions: 0/3 → room for all 3 setups
- Portfolio: €100.00 | Cash: €100.00
- Max drawdown per trade: ~€4.30 (4.3%)
```

> **Tip**: If the playbook shows "3/3 positions open", all setups will say **HOLD** — no new entries until you close something.

---

## 6. Escalation: "3/3 Positions → HOLD"

The system enforces a hard cap of **3 open positions** (configurable in `risk_profile.json`).

### What happens when you're maxed out?

- The pipeline **still runs** and generates setups.
- But the `RiskAgent` gates every setup with **HOLD** status.
- The playbook will show:

```
⚠️  PORTFOLIO FULL — 3/3 positions open
    New setups are shown for reference only.
    Close an existing position before entering.
```

### What to do?

1. **Review your open positions** — is any close to TP or SL?
2. **Close a losing trade** if the thesis is broken.
3. **Wait** — swing trading is about patience, not action.
4. **Never override the cap** by manually adding a 4th position. The sizing math assumes max 3.

---

## Quick Reference Card

```
Morning routine:
  1. python run_daily.py
  2. Open reports/YYYY-MM-DD/playbook.md
  3. Like a setup? → Place limit order on Binance
  4. Update positions.json
  5. Done. Go live your life.

Key files:
  positions.json          ← your portfolio state
  config/risk_profile.json ← risk rules & sizing
  reports/YYYY-MM-DD/     ← daily playbooks
  run_daily.py            ← the one command you run
```
