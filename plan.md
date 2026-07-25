# The Tenth Floor AI — Project Plan

> **Historical document.** This is the plan as written in early 2026. Phase 1
> was built and the pipeline ran live for about a week in April 2026, then
> stopped — the later phases, decision gates, and null-hypothesis criteria were
> never reached. It is kept because the pre-committed stopping rules are the
> part worth reading.
>
> The body below is as written, with two exceptions: the Phase 1 status list
> has been corrected (it was never updated as the work landed), and third-person
> references to me have been made first person. Figures reflect the project as
> it stood when written — the universe was later trimmed from 36 assets to the
> 20 in `config/universe.json`. See [README.md](README.md#results) for what
> actually happened.

---

## The Goal

Build a personal AI-assisted swing trade research system with verified positive expectancy over 12 months of forward testing. This is not a commercial product. It exists to generate a track record that justifies deploying real capital when income grows.

**Success definition:** After 12 months of on-demand paper trading — 50%+ win rate with average R:R above 2.0. Every published signal journaled in Notion. Outcomes resolved automatically as phase 2 of each pipeline run.

**What this is NOT:**
- A commercial product (shelved until track record is proven)
- A backtested system (forward testing only — cleaner data, no overfitting risk)
- An automated service — the pipeline never runs on a schedule. It runs when I click a button in the dashboard. There is no cron, no systemd timer, no auto-start.
- A system for someone else — this is personal, local, free

---

## Decision Filters

> Before adding any feature or changing anything, answer these questions first.

**The feature filter:** Does this improve (a) signal quality, (b) operational reliability, or (c) journaling? If no to all three — do not build it.

**The shiny object rule:** Every week there will be a new tool, model, or technique that looks interesting. The filter question is the only thing that matters. If it doesn't pass, ignore it completely.

**The model upgrade filter:** Before upgrading models, answer — do I have data showing the current model is the bottleneck? If the win rate is low but monthly Opus review of reasoning patterns hasn't been done, the model is not the problem. Fix the prompts first.

**The spending filter:** Before spending money on API access or premium models, answer — have I completed 60 days of paper trading with the free local setup? If no, wait.

---

## Final Status

**Phase 1 — Fix the Foundation: complete.**

- [x] Step 1: Codebase cleanup
- [x] Step 2: Signal quality iteration (rubric exit gate — see Step 2)
- [x] Step 3: Event emitter added to pipeline
- [x] Step 4: FastAPI backend built
- [x] Step 5: React dashboard built
- [x] Step 6: Docker integration
- [x] Step 7: End to end test — pipeline runs from browser

Steps 1 and 3–7 are evidenced in the repository: `src/tenth_floor/events.py`,
`src/tenth_floor/api/` (FastAPI + WebSocket), `dashboard/` (Vite + React), and
`Dockerfile` / `docker-compose.yml` (vllm, api, dashboard, pipeline). Discord
and Notion publishing landed alongside them in
`src/tenth_floor/notifications/`. Step 2 was prompt iteration rather than
code — the TradeAnalyst and RiskReviewer prompts went through several rounds —
but the per-signal rubric grading lived in the Notion journal, which has not
survived, so that part is no longer auditable.

**Phase 1 was complete when:** the pipeline ran from the browser button with real-time asset-by-asset feedback, AND 8 of the last 10 generated signals passed all 5 quality rubric criteria (see Step 2), AND none of those 10 made me cringe on read.

Phase 2 was where the experiment stopped — see [README.md](README.md#results).

---

## The 365-Day Plan

### Phase 1 — Fix the Foundation (Now → 30 days)

The only phase where coding is allowed. Two priorities only.

**Priority 1: Signal quality**

Run the pipeline. Read every output. If reasoning feels templated, generic, or starts with the same macro framing every time — iterate on TradeAnalyst and RiskReviewer prompts.

**The 5-criteria quality rubric** (binary, applied to every published signal):

1. References at least 2 specific price levels (entry, SL, TP, structural) by number — not adjectives
2. References at least 1 specific indicator reading (RSI value, MACD state, EMA position) — not "momentum looks good"
3. Names a specific macro condition relevant to *this asset class* (not generic "macro is mixed")
4. Articulates what would *invalidate* the thesis, not just what supports it
5. Reads as asset-specific — would not be plausible if you swapped the symbol for a different asset in the same class

Self-grade each signal during journaling — takes <30 seconds. Use Opus only as a tiebreaker when uncertain about a specific criterion (Opus stylistic bias makes single-number scoring unreliable, but it's fine for adjudicating "does this reference a specific price level: yes/no").

**Phase 1 exit gate:** 8 of the last 10 generated signals pass all 5 criteria, AND I don't cringe at any of them on second read.

**Priority 2: Operational dashboard**

Build the web dashboard so the pipeline is runnable from a browser with real-time feedback. Spec is in `dashboard/DASHBOARD_SPEC.md`. No more terminal anxiety.

---

### Phase 2 — Run the Experiment (30 → 90 days)

Zero new features. Zero code changes unless something is broken. Prompt edits during the monthly review do *not* count as features.

- Run the pipeline when you sit down at the machine — no fixed cadence, no cron. Aim for most days, accept that some will be skipped.
- Journal every published signal in Notion — one sentence reaction when generated, one sentence post-mortem when resolved
- Self-grade signals against the 5-criteria rubric during journaling
- Monthly AI review — paste full journal into Opus, ask for patterns in wins vs losses
- One round of prompt adjustments per month based on review findings (one PR, one redeploy)

**The metric that matters: expectancy in R units.**
Expectancy per trade = mean R outcome where winners contribute +planned_R, losers contribute −1R, and expired signals contribute 0. This is the *one* number that determines whether there is edge. Win rate and R:R are decompositions of it and can mislead individually (35% win rate at R:R 3.5 has positive expectancy; 60% at R:R 1.2 has negative).

**A note on "days" below.** Because the pipeline is manually triggered, calendar days and resolved-trade counts can drift apart — a week with no runs produces no new trades. All gates below are **driven by N (resolved trade count), not by elapsed days**. Day numbers are rough expectations only; if N is behind schedule, the gate simply waits.

**N ≈ 30-50 = look but don't decide.** The 95% CI on expectancy is still wide enough that the gates fire on noise. Compute the numbers, write down a hunch, but make no structural decisions yet.

**Decision gates fire once N ≥ 50 resolved trades:**

| Lower 95% CI bound on expectancy | Action |
|---|---|
| > +0.2R | Continue confidently — strong evidence of edge |
| in [−0.2R, +0.2R] | Continue cautiously — edge unproven, not falsified |
| < −0.2R | Diagnose before continuing — likely no edge in current config |

If N < 50 when day 90 arrives, just keep running until N hits 50 — the gate is N-driven, not time-driven.

---

### Phase 3 — Diagnose and Improve (90 → 180 days)

Add context enrichment only where it addresses specific failure patterns from the journal.

- RSS feeds — only if signals are consistently missing news-driven moves
- Earnings calendar — only if equity signals fail around earnings dates
- FRED 10Y yield — only if macro reasoning is wrong about rate-sensitive assets
- Model upgrade test — only if 60 days of data shows reasoning quality is the bottleneck

---

### Phase 4 — The Real Decision (180 → 365 days)

Six months of verified forward-tested signals. Data tells you whether to deploy real capital.

- Positive expectancy proven → deploy small real capital as income grows
- Not proven → 6 months of data shows exactly what to fix
- Commercial path → reopens here if track record is real. Not before.

---

### Null Hypothesis Acceptance Criteria

Pre-committed now, while it's cheap. Without this, the path of least resistance at month 6 will always be "iterate prompts more."

**Once N ≥ 80 resolved trades (expected around month 6 at typical cadence), if the lower 95% CI bound on expectancy remains below +0.1R, conclude that LLM-discretionary swing trading on daily candles does not have edge in the current configuration.**

Stop iterating on prompts and context. Choose one of:

1. **Shelve.** Accept the negative result, write up findings, redirect time. A clean negative result is more valuable than another six months of motivated iteration.
2. **Pivot the mechanism.** Change something *structural* (timeframe, asset selection rule, regime gating, ensemble across multiple LLMs, switch to short-only or pairs trading) and start a *new* 6-month experiment as v5. Do not blend old and new data.

**What is NOT on the menu at month 6 if the null holds:** more RSS feeds, better prompts, model upgrades, more context. Those are tweaks; if the mechanism is the problem, tweaks won't save it.

The discipline this enforces: month 6 either becomes a clean "this works, deploy capital" or a clean "this doesn't, stop or pivot" — no third option of perpetual tweaking.

---

## Step-by-Step Coding Plan (Phase 1)

### Step 1 — Codebase cleanup (Day 1)

Delete these files — they are not needed for personal use and add cognitive overhead:

```
src/tenth_floor/social/                     # entire directory
src/tenth_floor/notifications/discord_notifier.py
GTM.md
research.md
src/tenth_floor/backtest.py
```

Remove from `.env.example`, `.env.3090`, `.env.5090` — these two lines only, nothing else:
```
DISCORD_WEBHOOK_URL
DISCORD_TWEET_WEBHOOK_URL
```

Do NOT touch the hardware profile variables (VLLM_MODEL, VLLM_MAX_MODEL_LEN, VLLM_GPU_UTIL, VLLM_MAX_NUM_SEQS) or Langfuse keys.

After deleting: run `pytest` and confirm all tests still pass. Commit with message: `chore: remove commercial and social layer for personal use refactor`

---

### Step 2 — Signal quality iteration (Days 2-7)

Run the pipeline. Read every signal output carefully. Apply the Opus test.

Files to focus on:
- `src/tenth_floor/agents/trade_analyst.py` — prompt in `_SYSTEM_PROMPT`
- `src/tenth_floor/agents/risk_reviewer.py` — prompt in `_SYSTEM_PROMPT`
- `src/tenth_floor/agents/macro_analyst.py` — prompt in `_SYSTEM_PROMPT`

Goal: each signal should read like a genuine asset-specific trade thesis, not a generic financial chatbot response. The reasoning should reference specific price levels, specific indicator readings, specific macro context for that asset — not boilerplate.

Hard cap: 7 days maximum on this step. Ship the dashboard, then continue iterating prompts during the live experiment.

---

### Step 3 — Event emitter in pipeline ✅ COMPLETE

Lightweight pub/sub event bus at `src/tenth_floor/events.py` (`EventBus` + `PipelineEvent`), wired into every stage of `src/tenth_floor/main.py`.

**Events emitted:**
```
pipeline_started     # total asset count, profile, dry_run, max_daily_signals
macro_complete       # regime, VIX, F&G, DXY, per-class outlook, reasoning
asset_analyzed       # symbol, asset_class, action, confidence, entry/SL/TP, rationale
validation_result    # symbol, passed/failed, reward_risk_ratio or reason
reviewer_decision    # symbol, verdict, conviction, reasoning, risk_notes
pipeline_complete    # published_count, approved_count, funnel, elapsed_seconds
pipeline_error       # error_type, message
```

The bus stores events per `run_id` so a late WebSocket subscriber can replay the full history of an in-progress run. Subscriber exceptions are caught and logged. Single-process, in-memory, threadsafe. 139 tests pass (15 new event bus tests).

---

### Step 4 — FastAPI backend (Days 11-14)

Build `src/tenth_floor/api/` as a proper Python module (NOT a separate `dashboard/backend/` — keeps imports clean, avoids a second dependency graph, lives alongside the code it serves).

**Locked architecture decisions (from the Fork 1-3 debate):**

- **Single global WebSocket.** One endpoint `/ws`, client filters by `run_id` in the event payload. No per-run URLs — the dashboard connects on page load before any run exists.
- **Replay on connect.** When a WS client connects, the backend replays all events for the most recent run, in order, then streams live events. Makes refresh-mid-run non-destructive, which matters because manual-run dashboards get refreshed constantly.
- **Backend owns the trigger.** `POST /runs` spawns the pipeline in a background thread, returns the `run_id` immediately, events stream over `/ws`. CLI (`python -m tenth_floor.main`) stays for dev/debug only; the dashboard button is the operational path.
- **One run at a time.** A second `POST /runs` while one is in flight returns `409 Conflict`. Simpler state, one user, one brain.
- **Outcome checker runs as phase 2 of every pipeline run automatically.** After `pipeline_complete` fires, the same background thread runs `check_outcomes.py` and emits `outcome_check_started` / `outcome_resolved` / `outcome_check_complete` events over the same WS. No separate button, no forgotten step — the whole "pipeline → resolve yesterday's signals" cycle is one click. (Adds two new event types to `events.py`.)
- **Local-only, no auth.** FastAPI binds to `127.0.0.1` only. No login, no tokens, no CORS gymnastics beyond the Vite dev server origin (`http://localhost:5173`). Never expose this to the network.
- **Sync pipeline in a thread, not async rewrite.** `run_pipeline` is a long sync function full of blocking I/O (ccxt, yfinance, vLLM HTTP calls). Converting it to asyncio is scope creep. The API runs the pipeline in a `threading.Thread`; the async WS handler reads from an `asyncio.Queue` fed by the event bus subscriber.

**Module layout:**
```
src/tenth_floor/api/
├── __init__.py
├── app.py        # FastAPI app, lifespan, CORS, 127.0.0.1 bind, static file mount
├── runs.py       # POST /runs (thread spawn + auto outcome check), GET /runs/{id}
├── signals.py    # GET /signals, GET /signals/{id}, GET /stats — SQLite reads
└── ws.py         # /ws — subscribe to event_bus, replay latest run on connect
```

**Endpoints:**
- `POST /runs` — spawn pipeline + outcome check in background thread, return `{"run_id": "..."}` or `409` if a run is already active
- `GET /runs/active` — current running `run_id` or null (dashboard uses this on cold open to decide whether to show the live pane or the idle pane)
- `GET /runs/{run_id}` — full event history for a run (replay endpoint for non-WS clients / tests)
- `GET /signals` — all signals from SQLite with filters (status, asset_class, date range)
- `GET /signals/{signal_id}` — single signal with full reasoning
- `GET /stats` — expectancy in R with 95% CI, win rate, avg R:R, open count, N resolved
- `WS /ws` — replay latest run on connect, then stream live events

**Launch command:** `uvicorn tenth_floor.api.app:app --host 127.0.0.1 --port 8000`.

**Test plan:** Unit tests for each endpoint with TestClient + mocked event bus. Integration test that uses the same `_build_patches` harness from `test_main.py` to run a full pipeline through `POST /runs` and assert the full event sequence arrives over the WS in the correct order. No live LLM calls in tests.

---

### Step 5 — React dashboard (Days 15-25)

**Stack:** Vite + React 19 + Tailwind v4 + pnpm. Framer Motion for state transitions. Visx for charts (more control than Recharts, less AI-looking).

**Visual quality bar (non-negotiable).** This dashboard is opened daily for a year. Friction compounds, so does aesthetic fatigue. The bar:

- **Reference tier:** Linear, Vercel dashboard, Stripe dashboard. Not "Bootstrap admin template," not "ChatGPT-generated React boilerplate."
- **Design system first.** Before writing components, create `dashboard/DESIGN.md` with color tokens, type scale, spacing scale, motion principles, and component primitives. All components consume tokens — no inline magic numbers.
- **Motion rule: glow = state transition, never decoration.** Every animation must serve a state transition (idle → loading, queued → analyzing → result, value updating). No decorative motion, no looping glows on idle elements, no breathing gradients, no floating particles. Exceptions: Run button idle breathing glow (single CTA, needs to pull the eye) and the `analyzing` asset card teal pulse (state indication).
- **Typography.** Geist + Geist Mono via Google Fonts. Tabular figures for all numeric KPIs so digits don't jitter.
- **Empty/loading/error states are designed, not afterthoughts.** Skeleton loaders match real component dimensions. Empty states explain *why* and *what next*.
- **Dark only.** No light mode, no theme toggle.
- **Done = I would screenshot it and post it.** If you wouldn't, it's not done.

**Design tokens (locked):**
- Background: `#0A0712` (deep indigo-near-black)
- Text: `#F0F4FF` with opacity cascades /70 /50 /40 /30
- Purple primary: `#B47BFF` · Purple light: `#D4B3FF` · Purple deep: `#7C3AED`
- Teal accent: `#5EEAD4` (**used only on the `analyzing` asset card state, nowhere else**)
- Surface: `rgba(255,255,255,0.03)` · Border: `rgba(255,255,255,0.06)`
- Brand gradient: `linear-gradient(135deg, #F0F4FF 0%, #D4B3FF 40%, #B47BFF 75%, #7C3AED 100%)`
- Background atmosphere: single faint radial purple wash top-right. No grid, no animated gradients.
- Radius: 12px for cards
- Fractal noise overlay: 0.025 opacity, mix-blend overlay (preserved anti-AI trick)

**Structure — 2 nav views + modal runner:**

**View 1 — Track Record (landing, default)**
- Hero: date, idle/running status line, prominent **Run** button (purple gradient, subtle breathing glow)
- KPI strip: expectancy in R units with 95% CI, win rate %, avg R:R achieved, open positions (numbers tween 0 → value on load, tabular figures)
- Equity curve: Visx LineChart showing **cumulative R** over time (not cumulative R:R — that's not a real number). Draws on scroll-into-view via path-length animation.
- Concentration band: small per-day badge on the equity curve showing asset-class diversity of that day's published set (max share of any single class). Lets me eyeball whether bad days correlate with concentrated days.
- Today's signals — **three tiers**, collapsing visual weight:
  - **Published (top 5)** — full cards, entry/SL/TP, rationale, mini price chart
  - **Session signals** — compact chip row, 24h visibility, clickable to drawer
  - **Skipped** — collapsed by default, expandable for audit

**View 2 — Signal Archive**
- Card stack layout (vertical list, one signal per row ~120px tall), newest first
- All statuses (PENDING / OPEN / HIT_TP / HIT_SL / EXPIRED), not resolved-only. This is the logbook; Track Record is the scoreboard.
- Sticky filter bar: asset, status, date range, asset class, conviction. Default filter: last 30d.
- Click row → drawer slides in from right with full LLM rationale, MAE/MFE, price action since publish
- Notes field per signal (syncs to Notion when integration is added later)

**Runner — full-screen modal (not a nav view)**
- Summoned automatically when the WebSocket receives `pipeline_started` (triggered by the Run button on Track Record). Full-viewport dark overlay with backdrop blur.
- Phase rail top: Macro → Analyze → Review → Publish, sweeps left-to-right as pipeline progresses
- Asset card grid center: cards stream state changes live via WebSocket
  - **Queued** — flat glass, text/40
  - **Analyzing** — teal border pulse + shimmer line (the one teal use)
  - **Proposal** — purple border (TradeAnalyst BUY)
  - **Approved** — purple glow + purple gradient fill (RiskReviewer passed)
  - **Rejected** — opacity 0.3, line-through (RiskReviewer skipped)
  - Cards glide between columns via Framer Motion `layoutId` as they advance
- Collapsible log console bottom: raw event stream
- Auto-dismisses on `run_complete` after 2-second victory beat (approved cards pulse once). ESC to force-close. Track Record updates underneath with the new signals.

**Signal tiers:**
- Published (top 5) — permanent, tracked for outcomes
- Session signals (runner-up BUYs) — visible 24 hours, then archived
- Skipped assets — visible during run only

---

### Step 6 — Docker integration (Days 26-28)

Add dashboard as a second service in `docker-compose.yml`:

```yaml
dashboard:
  build:
    context: ./dashboard
  ports:
    - "3000:3000"
  volumes:
    - ./data:/app/data
  environment:
    DB_PATH: /app/data/playbook_history.db
```

Frontend served as static files by the FastAPI backend. Available at `localhost:3000`.

---

### Step 7 — End to end test (Days 29-30)

- Run full pipeline from browser button
- Watch every asset card appear in real time
- Confirm signals persist in SQLite
- Confirm track record view updates
- Fix anything broken
- Declare Phase 1 complete and start journaling

---

## Signal Configuration

Signal cap for personal use: **5 per run** (raised from 2 for commercial, not unlimited — quality over volume still matters for a clean experiment).

**Three signal tiers:**
1. Published (top 5) — permanent in SQLite, tracked for outcomes, shown in track record
2. Session signals — TradeAnalyst BUYs that didn't make the cut, archived after 24 hours
3. Skipped — visible during run only for feedback

---

## Journaling in Notion

**Database:** "Signal Journal" with these columns:
- Date, Asset, Asset class, Conviction
- Entry, SL, TP, Planned R:R
- Signal reasoning (full text from dashboard)
- Your reaction (one sentence — does this make sense?)
- Outcome (Won / Lost / Open / Expired)
- Post-mortem (one sentence — was the reasoning right?)
- Monthly review tag

**The daily habit (5 minutes max):**
1. Open dashboard, see today's signals
2. For each published signal: create Notion row, paste reasoning, write one sentence reaction
3. Once a week: check resolved signals, fill in Outcome and Post-mortem
4. Once a month: filter by month tag, copy to text file, paste into Opus for pattern analysis

**Notion API integration:** Planned for Phase 2 or 3 — pipeline will auto-create Notion rows when a signal is published. Not needed for Phase 1.

---

## What Stays, What Was Removed

**Removed (not needed for personal use):**
- `src/tenth_floor/social/` — tweet drafter, poster, Discord draft
- `src/tenth_floor/notifications/discord_notifier.py` — replaced by dashboard
- `GTM.md` — commercial go-to-market planning
- `research.md` — competitor analysis
- `src/tenth_floor/backtest.py` — raises NotImplementedError, misleading

**Kept and unchanged:**
- All three agents (MacroAnalyst, TradeAnalyst, RiskReviewer)
- All data fetching (ccxt, yfinance)
- SQLite schema and signal logger
- Outcome checker
- Universe config (36 assets)
- Docker / hardware profiles
- All tests (now 139 after removing commercial test files and adding event bus tests)

---

## Hardware

- Development: RTX 3090 (24GB VRAM) — use `.env.3090`
- Production runs: RTX 5090 (32GB VRAM) — use `.env.5090`
- Model: Qwen3-32B-AWQ via vLLM
- Future: upgrade to better model when positive expectancy is proven and income allows

---

*Last updated: April 2026*
*Next review: After 30 days (Phase 1 complete)*