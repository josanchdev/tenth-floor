🎯 Core Goals (NEVER change)

Manual Binance execution (NO auto-trading)
**SPOT ONLY / LONG ONLY** — no shorting, no futures, no margin
Daily Gmail playbook delivery
Streamlit UI for trade state management (close/edit positions + PnL)
Weekly swing trades (4h/1d), €100 budget → 2-3 positions max
Risk: 8% per trade, 1.2x ATR stops

📋 Daily Workflow

1. `python run_daily.py` → generates playbook.md → emails to GMAIL_USER
2. User reads Gmail → executes on Binance app (manual)
3. User opens Streamlit (`streamlit run streamlit/app.py`) → "Executed BTC" or "Close BTC +€12"
4. Streamlit → updates `positions.json` (portfolio_value, cash, open_positions)
5. Tomorrow → RiskAgent reads positions.json → respects budget/positions

🛠 Key Files & Their Jobs

docs/architecture.md:     data → agents → playbook flow
config/risk_profile.json: 8% risk, 1.2x ATR, max 2 positions
config/spot_only.json:    LONG-only enforcement flag
positions.json:           state (gitignored) – cash €100, open trades
models.py:                Pydantic schemas (OHLCVBar → PlaybookEntry)
src/data/market_data.py:  ccxt Binance cache (done ✓)
src/data/sentiment.py:    F&G + RSS headlines (done ✓)
src/features/ta_calculator.py: TA indicators via pandas-ta (done ✓)
src/features/pair_snapshot.py: PairSnapshot assembly (done ✓)
streamlit/app.py:         TODO: tabs (Dashboard, Open Trades, Playbook)
run_daily.py:             TODO: wire pipeline + Gmail send

📧 Gmail Integration

.env → GMAIL_USER=your@gmail.com, GMAIL_APP_PASS=abcd...
run_daily.py → smtplib → playbook.html to Gmail
Subject: "Copilot Playbook 2026-03-14"

🎛 Streamlit UX (v2)

Tab 1: Dashboard → PnL chart, €93 cash, 1/2 positions
Tab 2: Open Trades → BTC €33 @60k [Close +€12] [Edit]
Tab 3: Playbook → latest suggestions [Executed]

⚠️ NEVER Do These

- Connect ccxt with write API keys
- Auto-place orders
- LLM compute position size (Python only)
- Suggest trades < €20 (fees) → flag "⚠️ Fees too high"
- **Propose SHORT positions** → agents MUST output LONG only (spot trading)
- **Use futures or margin APIs** → Binance spot only
- **Generate SetupProposal with direction=SHORT** → RiskAgent MUST reject with "Spot only – no shorting"

✅ ALWAYS Do These (when implementing agents)

- Every agent system prompt MUST say: "Propose LONG positions only. Spot trading."
- StrategyAgent: direction=LONG always. If bearish → action=SKIP, never SHORT.
- RiskAgent: reject SHORT proposals, flag positions < €20.
- Load config/spot_only.json → enforce only_long=true.

📊 V1 Roadmap — 7 tasks remaining

Done (6/13):
1. ~~src/data/market_data.py~~ — ccxt Binance OHLCV + Parquet cache ✓
2. ~~src/data/sentiment.py~~ — Fear & Greed API + CoinDesk RSS ✓
3. ~~src/features/ta_calculator.py~~ — EMA, RSI, MACD, BB, ATR, OBV ✓
4. ~~src/features/pair_snapshot.py~~ — PairSnapshot assembly ✓
5. ~~docs/workflow.md~~ — daily user guide ✓
6. ~~config/ updates~~ — risk_profile.json, spot_only.json, positions_template.json ✓

Remaining (7 tasks to v1):
7. src/agents/quant_agent.py — trend regime + confidence (needs Langfuse prompts) ⬜
8. src/agents/sentiment_agent.py — risk narrative + bias label ⬜
9. src/agents/strategy_agent.py — LONG-only entry/SL/TP proposals ⬜
10. src/agents/risk_agent.py — position sizing + max-positions gate ⬜
11. src/report/builder.py + exporter.py — playbook Markdown/HTML ⬜
12. src/orchestration/pipeline.py + run_daily.py — wire everything + Gmail ⬜
13. streamlit/app.py — CRUD positions.json (Dashboard, Trades, Playbook tabs) ⬜

ALWAYS reference @PROJECT_CONTEXT.md for new tasks.