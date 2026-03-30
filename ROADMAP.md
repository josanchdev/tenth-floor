# Roadmap — The Tenth Floor AI

> **V4** is the active plan (approved 2026-03-30). Multi-asset universe
> expansion: crypto + US equities + ETFs + commodities.
>
> **V3** is complete. V2 is complete. See summaries below.

---

## V3 Summary (complete — 2026-03-27)

V3 addressed diagnostics, testability, and commercial readiness after V2
ran daily with zero signals (all 26 crypto pairs rejected by the gates
in bearish markets — the structural problem that led to V4).

**Delivered:**
- Pipeline diagnostics & funnel report (gate-kill stats to Discord every run)
- Historical backtester (90-day deterministic gate replay, no LLM needed)
- LLM retry logic (max_retries=3, timeout=30s via OpenAI client config)
- Config profiles (`--profile validation|production`, overlays on risk_profile.json)
- Failure alerting (error embeds to Discord on unhandled exceptions)
- LLM short-circuit (pre-filter by trend_score, 0 vLLM calls on bearish days)
- 26-pair universe with sector mapping + max 1 signal per sector
- RSI bullish divergence detection + capitulation bypass (Gate 1)
- DB migration system (numbered SQL files, auto-applied on init)
- GitHub Actions CI (pytest + ruff + mypy, Python 3.12–3.13)
- Dynamic price precision for sub-dollar coins
- Duplicate-safe Discord posting
- Market-price entries (entry at current price, structure-based SL/TP)

**Not delivered (absorbed into V4 or deferred):**
- Langfuse prompt management → do alongside V4 agent prompt updates
- Richer sentiment → replaced by V4 MacroAgent + 8-feed RSS expansion
- Structured logging → do alongside V4
- Pre-commit hooks, Docker Compose, Grafana — deferred, not urgent

**Known limitations (carried forward):**
1. QuantAgent confidence not calibrated against win rates (revisit at 30+ trades)
2. No per-asset sentiment → solved by V4 MacroAgent
3. BTC correlation guard is a heuristic → generalized in V4 to market-leader gates

---

---

# V4 Proposal — Multi-Asset Universe Expansion

> Status: **APPROVED** (2026-03-30).
> Refined through iterative discussion on sentiment architecture,
> overnight gap risk, scheduling, signal delivery, and naming.

---

## The Problem

The pipeline is technically correct. The gates work. But the system has a
structural business viability problem that no amount of gate tuning can fix.

**The 26 crypto pairs are not 26 independent opportunities — they are 1
market direction expressed 26 different ways.** When BTC enters a downtrend,
the entire crypto market follows within hours. The trend gate correctly kills
19-21 of 26 pairs every day during these periods. The system goes days or
weeks without a single approved signal.

This was invisible during design and only became apparent in production.
The backtester confirmed it: even with relaxed thresholds, crypto-only
universes produce clusters of signals during bull phases and complete
silence during bear phases. The sector diversity cap (max 1 per sector)
helps within a cycle but cannot manufacture opportunities that don't exist
in the underlying market.

**The compounding effect on the business:**
- No signals → no track record → nothing to sell
- No track record → no compelling social media content
- No content → no audience growth → no subscribers
- The pipeline is technically sound but commercially stuck

This is not a calibration problem. Lowering thresholds to force signals
would compromise the core philosophy (silence as default, publish only
when evidence is overwhelming). The problem is the input universe, not
the filter chain.

---

## The Thesis

Expand from crypto-only to a genuinely multi-asset universe: crypto, US
equities, ETFs, and commodities. Same pipeline, same 7 gates, same
philosophy. But applied to ~40 assets that are structurally uncorrelated.

**Why this works:**
- When crypto is in extreme fear, gold is often rallying (flight to safety)
- When tech stocks are in a downtrend, energy or healthcare may be trending
- When US equities are flat, crypto may be in a momentum phase
- The probability that *at least one asset class* is trending on any given
  day is dramatically higher than the probability crypto alone is trending

**Why this is not scope creep:**
- The TA indicators are mathematically identical across asset classes —
  RSI, EMA, MACD, Bollinger Bands work the same on AAPL as on BTCUSDT
- The trend scoring logic (`ta_calculator.py`) is already fully asset-agnostic
- The RiskAgent verdict logic is pure Python with config-driven rules
- The agent prompts are ~85% asset-agnostic; only language references "crypto"
- The Pydantic models (`PairSnapshot`, `PlaybookEntry`) carry no crypto assumptions

The system was accidentally designed to be multi-asset. The crypto-specific
coupling is concentrated in three modules: data fetching, sentiment, and
two BTC-specific gates. Everything else transfers with zero or trivial changes.

---

## Architectural Audit: What Changes, What Stays

### Fully Reusable (zero changes)

| Module | Why |
|--------|-----|
| `ta_calculator.py` | Pure pandas-ta math. Trend score, RSI divergence, swing levels — all universal |
| `data/models.py` | `PairSnapshot`, `TAIndicators`, `PlaybookEntry` — no crypto assumptions in the schemas |
| `agents/base.py` | LLM call pattern, JSON parsing, think-block cleaning — provider-agnostic |
| `agents/risk_agent.py` verdict logic | Pure Python: R:R check, confidence threshold, conviction tiers. Config-driven |
| `features/pair_snapshot.py` | Assembles snapshot from OHLCV + TA + sentiment. Asset-class agnostic |
| Signal logging (SQLite) | `UNIQUE(pair, timeframe, report_date)` works for any symbol format |
| Pipeline diagnostics/funnel | Gate kill counts are universal |
| Backtester | Replays gates deterministically — works for any OHLCV data |

### Needs Changes

| Module | What changes | Effort |
|--------|-------------|--------|
| `market_data.py` | New `YFinanceDataFetcher` alongside existing ccxt fetcher | Medium |
| `sentiment.py` → full redesign | Two-stage MacroAgent + per-asset context (see below) | High |
| `main.py` gates 4 & 7 | BTC-specific → config-driven market-leader gates | Medium |
| `main.py` scheduling | Two-pass pipeline (equities close, then crypto close) | Medium |
| `check_outcomes.py` | Per-asset-class check timeframe + market calendar awareness | Medium |
| `universe.json` | Restructure with `asset_class`, `class_leader`, `market_hours` | Medium |
| `risk_profile.json` | Per-class signal caps, entry confirmation rules | Low |
| SQLite schema | Add `asset_class` column to signals + pipeline_runs | Low |
| Discord notifier | Per-asset-class channel routing + entry confirmation labels | Low |
| Tweet drafter | Multi-asset content, not crypto-only | Low |
| Dashboard | Filter by asset class, multi-asset views | Medium |
| Agent prompts | Asset-class context injection (template variables, not rewrites) | Low |
| Package name | `tenth_floor` → `tenth_floor` (every import, pyproject.toml) | Medium |

---

## Sentiment & Macro Architecture (Major Redesign)

The current SentimentAgent is the weakest part of the pipeline for multi-asset.
It takes a single crypto Fear & Greed number, reads a few CoinDesk headlines,
and produces one global sentiment label. That was acceptable for 26 crypto pairs
that all share the same macro exposure. It is not acceptable for a universe
where "war in the Middle East" is bearish for airlines but bullish for defence
stocks and gold.

**The sentiment layer is one of the most important parts of V4.** It needs to
correctly differentiate how macro events affect each asset class — and
ideally each specific asset. Dollar weakening is bullish for BTC and gold.
Oil spikes are bearish for airlines and bullish for energy stocks. Rate cuts
are bullish for growth tech and bonds. The pipeline needs this causal
reasoning to make intelligent per-asset decisions.

### Two-Stage Architecture

**Stage 1: MacroAgent (new — runs once per pipeline)**

Ingests all available macro data and produces a structured macro briefing.
This is the causal reasoning layer.

Inputs:
- VIX level + 7-day trend (via yfinance — free, real-time, derived from
  actual options pricing, not surveys)
- Crypto Fear & Greed index + 7-day trend (existing, keep as-is)
- US 10-year Treasury yield + trend (via FRED API — free, no key needed
  for basic access)
- DXY (dollar index) level + trend (via yfinance)
- All RSS headlines across asset classes (see feed list below)
- Earnings calendar for equities in the universe (via yfinance —
  `ticker.calendar` gives next earnings date)

Output (structured, not free-text):
```json
{
  "macro_regime": "risk_off | risk_on | mixed | transitioning",
  "vix_level": 22.5,
  "vix_trend": "rising",
  "crypto_fg": 28,
  "crypto_fg_trend": "falling",
  "usd_trend": "weakening",
  "rate_environment": "cutting | holding | hiking",
  "cross_asset_narrative": "Risk-off environment driven by rising
    geopolitical tensions. USD weakening as flight to safety favours
    gold over dollar. VIX elevated but not panic. Crypto fear is high
    but decoupling from equity weakness — BTC holding relative to
    the broader selloff.",
  "asset_class_impacts": {
    "crypto": "Neutral-to-bullish. Extreme fear often marks bottoms.
      Dollar weakness is a tailwind. Watch for BTC divergence from
      equities as a strength signal.",
    "equities": "Bearish. Rising VIX + geopolitical risk = headwinds
      for growth. Defensive sectors (healthcare, utilities) may hold.
      Energy benefits from oil supply concerns.",
    "commodities": "Bullish for gold (safe haven + USD weakness).
      Oil elevated on supply risk but demand outlook weakening.",
    "bonds": "Bullish. Flight to quality + rate cut expectations
      favour long-duration treasuries."
  },
  "specific_alerts": [
    {"symbol": "NVDA", "alert": "Earnings report in 3 days — expect
      elevated volatility, technicals may be overridden by results"},
    {"symbol": "XOM", "alert": "Oil supply disruption headlines —
      bullish catalyst for energy sector"}
  ]
}
```

The LLM already knows macro relationships (dollar weakness → gold up,
rate cuts → growth stocks up, war → defence up / travel down). The
current problem isn't model knowledge — it's that the prompt never
asks for this reasoning. MacroAgent's job is to surface it.

**Stage 2: Per-asset context injection (replaces current SentimentAgent)**

The MacroAgent output gets paired with each asset's specific context
when feeding into StrategyAgent. Instead of every asset seeing the same
"F&G is 28, market is fearful" blob, each asset sees:

- The relevant slice of `asset_class_impacts` for its class
- Any `specific_alerts` that match its symbol
- The macro regime and key numbers (VIX, DXY, rates)
- Asset-class-specific F&G or volatility reading

This means StrategyAgent for NVDA sees "earnings in 3 days, elevated
volatility expected" while StrategyAgent for GLD sees "safe haven demand
rising, USD weakening — bullish macro tailwind." Same macro data,
different interpretation per asset.

### RSS Feed Expansion (Professional Outlets, Not Social Media)

**Why not Reddit/X:** X API is $100+/month for meaningful access, scraping
violates ToS and breaks constantly. Reddit API went paid in 2023.
Signal-to-noise ratio on both platforms is terrible — 95% memes, shilling,
and noise. Extracting actionable sentiment from social media requires its
own NLP pipeline. That's a second project, not a feature.

**What to use instead:** Professional financial RSS feeds are free,
structured, pre-filtered by editors, and reliable.

| Feed | Asset Classes | URL |
|------|---------------|-----|
| CoinDesk (existing) | Crypto | `coindesk.com/arc/outboundfeeds/rss/` |
| CoinTelegraph | Crypto | `cointelegraph.com/rss` |
| The Block | Crypto, macro | `theblock.co/rss` |
| Reuters Business | All | `feeds.reuters.com/reuters/businessNews` |
| MarketWatch | Equities, macro | `feeds.marketwatch.com/marketwatch/topstories` |
| CNBC Economy | Macro, equities | `cnbc.com/id/20910258/device/rss/rss.html` |
| Seeking Alpha | Equities | `seekingalpha.com/market_currents.xml` |
| Bloomberg (via RSS proxy) | All | Various sector feeds |

8 feeds × 10 items each = ~80 headlines per run. The MacroAgent
reads all of them and selects the most market-moving ones per asset
class. This is the same pattern as the current SentimentAgent but
with richer input and smarter output routing.

### Sentiment Data Sources by Asset Class

| Data Source | What It Tells Us | Cost | Asset Classes |
|-------------|-----------------|------|---------------|
| Crypto Fear & Greed (existing) | Crypto market sentiment 0-100 | Free | Crypto |
| VIX (via yfinance) | Equity volatility / fear gauge | Free | Equities, bonds |
| US 10Y yield (via FRED) | Rate environment, risk appetite | Free | All |
| DXY dollar index (via yfinance) | USD strength/weakness | Free | Crypto, commodities, forex |
| Earnings calendar (via yfinance) | Per-stock event risk | Free | Equities |
| RSS headlines (8 feeds) | News-driven sentiment | Free | All |

No CNN Fear & Greed scraping (fragile HTML scraping that breaks on
redesigns). VIX is better — it's derived from real options pricing,
available via yfinance as a standard ticker (^VIX), and updates in
real time. For equities, VIX > 25 maps roughly to "fear" territory,
VIX > 35 is panic. This is more grounded than any survey-based index.

---

## The Overnight Gap Problem

**This is a first-class design concern, not a footnote.**

The current pipeline assumes 24/7 markets: signal publishes, subscriber
enters immediately at market price. This is true for crypto. It is
dangerously wrong for equities.

Scenario: pipeline runs after US market close, approves a BUY on Tesla
at $250. Overnight, Elon Musk gets hospitalized. Tesla gaps down 15%
at open. The subscriber who trusted the signal is immediately underwater
with no chance to react. The entry zone was valid at close but
meaningless at open.

This is not an edge case — overnight gaps of 2-5% happen regularly on
individual stocks, and 10%+ gaps happen on earnings/news.

### Solution: Conditional Entry Zones

**Equity signals publish with explicit entry conditions, not blind
buy orders.** The Discord embed and signal record include:

```
⚡ BUY SIGNAL — AAPL (US Equity)
  Entry zone: $198.50 – $202.00
  ⚠️ ENTRY CONDITION: Valid only if AAPL opens within entry zone.
     If price gaps beyond this range, signal is VOID.
  Stop-loss: $191.20
  Take-profit: $215.80
  R:R: 2.4
```

This is honest, transparent, and how professional equity research works.
"Buy if price is in this range" not "buy no matter what."

**Implementation:**
- Add `entry_condition` field to `PlaybookEntry` (enum: `IMMEDIATE` for
  crypto, `CONDITIONAL_OPEN` for equities)
- StrategyAgent prompt includes: "For equities, entry is conditional on
  next-day open price being within the entry zone. If overnight news
  causes a significant gap, the setup is invalidated."
- Outcome checker: if an equity signal's next-day open is outside the
  entry zone, mark it as `VOID` (new status) — not `EXPIRED`, not
  `OPEN`, just void. It never activated.
- Discord embed includes the condition warning for equity signals
- Crypto signals remain `IMMEDIATE` — no change to current behaviour

**Future upgrade (not V4):** Pre-market confirmation pass. Run a quick
check at 9:00 AM ET using pre-market data. If pre-market price is within
entry zone, confirm the signal. If it's gapped significantly, void it
automatically before market open. This requires a second pipeline pass
and pre-market data access — doable but Phase 2 complexity.

---

## Pipeline Scheduling

The current pipeline runs once daily. This is a problem for multi-asset
because different markets close at different times:

- **US equities:** Close 4:00 PM ET (21:00 UTC in summer, 22:00 UTC in winter)
- **Crypto:** 24/7, daily candle closes at 00:00 UTC
- **Commodities (ETFs):** Same hours as US equities

If the pipeline runs at 00:00 UTC, crypto candles are fresh but equity
candles are 3 hours stale. If it runs at 21:00 UTC, equity candles are
fresh but the crypto daily candle hasn't closed yet.

### Solution: Two-Pass Schedule

**Pass 1 — Equities pass (21:30 UTC / 5:30 PM ET):**
- Fetch equity + ETF + commodity OHLCV (market just closed, candles final)
- Fetch all macro data (VIX, yields, DXY, RSS headlines)
- Run MacroAgent (produces macro briefing for all asset classes)
- Run pipeline gates for equity/ETF/commodity assets only
- Publish any approved equity signals with `CONDITIONAL_OPEN` entry type
- Cache the MacroAgent output for Pass 2

**Pass 2 — Crypto pass (00:15 UTC):**
- Fetch crypto OHLCV (daily candle just closed)
- Fetch updated crypto F&G (if it's changed since Pass 1)
- Load cached MacroAgent output from Pass 1 (macro context is still fresh
  — only 3 hours old, and macro data doesn't change overnight)
- Run pipeline gates for crypto assets
- Publish any approved crypto signals with `IMMEDIATE` entry type

**Why this works:**
- Each asset class is analyzed with its freshest possible data
- MacroAgent runs once (efficient), output is reused
- Crypto subscribers get signals at midnight UTC (same as current)
- Equity subscribers get signals after market close (actionable next morning)
- The two passes share the same macro context (coherent cross-asset reasoning)

**Implementation:** `run.sh` calls `python -m tenth_floor.main --asset-class equity`
at 21:30 UTC and `python -m tenth_floor.main --asset-class crypto` at 00:15 UTC.
Or a single `--all` flag that runs both passes sequentially with a sleep.
No orchestration framework needed — cron is sufficient.

---

## Signal Caps & Cross-Asset Diversity

Current: max 2 signals per day (production), regardless of asset class.

With 40 assets across 4 classes, a flat cap of 2 could produce 2 gold
signals and zero equity/crypto signals. This is technically correct
(the 2 best setups win) but commercially suboptimal — subscribers want
to see the best opportunity per market, not two from the same niche.

### Solution: Featured Feed + Uncapped Per-Class Channels

Two layers of signal delivery:

**Layer 1 — Featured signals (the editorial voice):**

```json
{
  "max_featured_signals": 3,
  "max_per_asset_class": 2,
  "max_per_sector": 1
}
```

- **Max 3 featured** (the headline picks, best across all markets)
- **Max 2 per asset class** (prevents all-crypto or all-equity days)
- **Max 1 per sector** (existing, prevents 3 L1 chain signals)
- Posted to `#daily-signals` — the main channel, the product's curated output

The story of "the AI reviewed 36 markets and found exactly 2 worth
acting on" is what makes the product credible. Scarcity = perceived value.

**Layer 2 — Per-class channels (uncapped, full output):**

Every signal that passes all gates gets posted to its asset-class channel:
- `#crypto-signals` — all approved crypto signals
- `#equity-signals` — all approved equity/ETF signals
- `#commodity-signals` — all approved commodity signals

No cap on these channels. A subscriber who only trades crypto sees every
crypto signal that survived the full gate chain, even if it wasn't in
the top 3 featured. A subscriber who trades everything follows `#daily-signals`
for the curated picks.

This gives both: the selectivity that makes the product credible, and the
completeness that keeps specialists engaged. The featured feed is the
product's brand voice. The per-class channels are the full unfiltered output.

---

## Proposed Universe (V4 Launch)

The goal is uncorrelated exposure across asset classes, not maximum coverage.
Every asset must be liquid, available via free data APIs, and tradeable by
retail investors without special accounts.

### Crypto (12 pairs — trimmed from 26)

Keep the most liquid, least redundant crypto pairs. Having 9 L1 chains
is the current redundancy problem.

| Symbol | Sector | Rationale |
|--------|--------|-----------|
| BTCUSDT | btc | Market leader, mandatory |
| ETHUSDT | l1 | #2 by cap, distinct thesis (smart contracts) |
| SOLUSDT | l1 | Performance leader among alt-L1s |
| BNBUSDT | exchange | Exchange token, different dynamics |
| LINKUSDT | infra | Oracle infrastructure, unique niche |
| UNIUSDT | defi | DeFi blue chip |
| AAVEUSDT | defi-lending | Lending protocol, different DeFi sub-sector |
| DOGEUSDT | meme | Meme sector representative |
| ARBUSDT | l2 | L2 representative |
| FETUSDT | ai | AI narrative representative |
| ONDOUSDT | rwa | Real-world assets, distinct thesis |
| XRPUSDT | payments | Payments sector representative |

### US Equities (15 stocks)

Diversified across sectors that move independently. No more than 2-3 per
sector. Focus on liquid large-caps with clean daily candle data.

| Symbol | Sector | Rationale |
|--------|--------|-----------|
| AAPL | tech-hardware | Mega-cap, often diverges from growth tech |
| MSFT | tech-software | Enterprise software, different cycle from hardware |
| NVDA | semiconductors | AI/GPU narrative, high-beta |
| AMZN | tech-cloud | Cloud + retail hybrid |
| GOOGL | tech-ads | Ad-revenue driven, different from SaaS |
| META | tech-social | Social/metaverse, distinct from other tech |
| JPM | financials | Bank sector leader |
| UNH | healthcare | Healthcare sector leader, defensive |
| JNJ | healthcare-pharma | Pharma, very defensive, low correlation to tech |
| XOM | energy | Oil major, counter-cyclical to tech |
| CVX | energy | Second energy name for sector depth |
| HD | consumer | Home improvement, consumer discretionary |
| PG | consumer-staples | Defensive consumer staple |
| CAT | industrials | Infrastructure/construction, cyclical |
| NEE | utilities | Utility, very low correlation to everything |

### ETFs (6 indices)

Broad exposure and sector rotation signals. These also serve as class
leaders and macro gauges for the gate logic.

| Symbol | Tracks | Rationale |
|--------|--------|-----------|
| SPY | S&P 500 | US broad market leader (class leader for equities) |
| QQQ | Nasdaq 100 | Tech-heavy index, class leader for tech sector |
| XLE | Energy sector | Sector ETF for energy rotation |
| XLF | Financials sector | Sector ETF for financials rotation |
| XLV | Healthcare sector | Sector ETF for healthcare rotation |
| TLT | 20+ year Treasuries | Bond proxy, inverse correlation to equities |

### Commodities (3 via ETFs)

| Symbol | Asset | Rationale |
|--------|-------|-----------|
| GLD | Gold | Safe haven, inverse to risk assets, class leader for commodities |
| USO | Crude oil | Energy commodity, macro-driven |
| SLV | Silver | Precious metal, partially industrial |

### Universe Total: 36 assets

**Correlation structure:** The universe deliberately includes assets that
historically move in opposite directions:
- Gold (GLD) vs. risk assets (QQQ, crypto) — flight-to-safety inverse
- Treasuries (TLT) vs. equities (SPY) — classic negative correlation
- Energy (XOM, XLE) vs. tech (NVDA, QQQ) — sector rotation
- Healthcare (UNH, XLV) vs. everything — low beta, independent
- Crypto vs. traditional — partially correlated but with distinct phases
- USD (DXY) vs. crypto + gold — inverse on dollar weakness

This means: when the trend gate kills all crypto (bear market), it's likely
that GLD, TLT, or defensive equities (UNH, JNJ, PG) are trending. The
pipeline should produce 1-3 signals on most days, not because thresholds
are relaxed, but because the opportunity set is genuinely diversified.

---

## Market Leader Gates (Generalized from BTC)

Gate 4 (BTC relative strength) and Gate 7 (BTC correlation guard) express
a valid principle: *when the market leader is weak, followers are riskier.*
This generalizes to any asset class.

| Current (crypto-only) | Generalized (V4) |
|----------------------|-------------------|
| BTC is the market leader | Each asset class has a configured leader |
| Compare alt performance vs BTC | Compare asset performance vs its class leader |
| If BTC rejected → cap alt signals to 2 | If class leader rejected → cap class signals |

**Class leaders (configured in `universe.json`):**
- Crypto: BTCUSDT
- US Equities: SPY
- Commodities: GLD

The leader is always analyzed first. If the leader fails any gate, the
correlation guard caps that entire class to `max_per_asset_class` signals
(or fewer). This preserves the existing conservative principle without
hardcoding BTC.

The relative strength gate also generalizes: in fear environments, skip
assets underperforming their class leader (same math, config-driven
leader symbol instead of hardcoded "BTCUSDT").

---

## What the LLM Needs to Reason Well About Non-Crypto

The gates are mostly deterministic Python. But QuantAgent and StrategyAgent
use LLM reasoning to classify trends and propose setups. Can Qwen3-32B
reason equally well about Apple stock as BTCUSDT?

**Arguments that it can:**
- TA interpretation is domain-generic. "RSI at 28 with bullish divergence
  while price holds above EMA 200" means the same thing on any chart
- Qwen3-32B was trained on massive financial text including equity
  analysis, commodity reports, and forex commentary
- The LLM doesn't compute anything — it classifies trend regimes and
  selects confluence factors. This is pattern recognition on indicator
  values, identical across asset classes
- The structured output schema (trend_regime, signals list, confidence)
  is asset-agnostic

**Arguments that it might struggle:**
- Crypto has 24/7 momentum; equities have overnight gaps, earnings
  surprises, ex-dividend dates that disrupt technical patterns
- Commodity markets have contango/backwardation dynamics — but since
  we're trading ETFs (GLD, USO) not futures, this is abstracted away

**Mitigation:** Each asset receives an `asset_class` and `asset_context`
field in its snapshot. The LLM sees a 2-3 sentence briefing:
- Crypto: "24/7 market, high volatility, momentum-driven, correlated to BTC"
- Equity: "Market hours 9:30-16:00 ET, overnight gaps common, earnings
  dates can override technical signals. Entry is conditional on next-day open."
- Commodity ETF: "Tracks spot commodity price, macro-driven, lower
  volatility than crypto. Entry is conditional on next-day open."
- Bond ETF: "Inverse correlation to equities, rate-sensitive, low volatility"

Plus, the MacroAgent's per-asset-class impact brief and any specific
alerts (earnings dates, news catalysts) are injected into the prompt.
This gives each agent call the full macro + asset-specific context
it needs to reason well.

This is empirically testable before launch. Run the backtester on 90
days of equity OHLCV and compare trend classifications against actual
outcomes. If equity signals are garbage, we'll see it in backtesting
before publishing anything.

---

## Outcome Checker Changes

Current: walks 4h candles on Binance to check SL/TP hits. Crypto is 24/7
so every 4h candle exists.

Stocks trade 6.5 hours per day. 4h candles don't align to market hours.

**Changes for V4:**
- Check timeframe is configurable per asset class (4h for crypto, 1d for equities)
- New signal status: `VOID` — equity signal where next-day open was outside
  entry zone. Never activated, does not count as a win or loss.
- Market calendar awareness via `exchange_calendars` package — skip weekends
  and holidays when walking candles
- Outcome checker fetches from the correct data provider per asset class
  (ccxt for crypto, yfinance for equities)
- Expiry period remains 14 calendar days for crypto, 10 trading days for
  equities (equivalent duration, adjusted for weekends)

---

## Package Rename (complete)

Renamed from `crypto_swing_copilot` to `tenth_floor` as the first step
of V4. All source files, tests, imports, pyproject.toml, env vars, and
documentation have been updated. The package is `the-tenth-floor` on pip,
with module path `src/tenth_floor/`.

---

## Risks

### 1. Quality dilution (HIGH risk, MEDIUM mitigation)

Adding 24 non-crypto assets risks being mediocre at everything instead of
excellent at one thing. Agent prompts, thresholds, and the backtester were
all tuned on crypto dynamics.

**Mitigation:** Launch equities in validation mode alongside crypto
production. Run both in parallel for 30+ days. Compare trend classification
accuracy and signal quality before publishing equity signals to Discord.
The config profile system already supports this.

### 2. yfinance reliability (MEDIUM risk, LOW mitigation)

yfinance is an unofficial Yahoo Finance wrapper. Yahoo can change their
API, add rate limits, or block access at any time.

**Mitigation:** yfinance has been stable for years. For 30 tickers once
per day, rate limiting is not a concern. If it breaks, switch to Polygon.io
($29/month). Future problem, not launch blocker.

### 3. Overnight gap risk (HIGH risk, addressed by design)

Equity signals published after close can be invalidated by overnight news.
A subscriber who blindly buys at next open could enter a gapped-down stock.

**Mitigation:** Conditional entry zones (see section above). Signals are
explicitly conditional on next-day open being within the entry zone.
`VOID` status for signals that never activated. This is transparent
and honest — the pipeline acknowledges that it cannot predict overnight events.

### 4. Market hours / scheduling complexity (MEDIUM risk, MEDIUM mitigation)

Weekends, holidays, half-days, circuit breakers. Different close times
for crypto vs equities.

**Mitigation:** Two-pass schedule (equities at 21:30 UTC, crypto at
00:15 UTC). `exchange_calendars` package for trading day awareness.
For daily candle swing trades, this complexity is manageable.

### 5. Sentiment quality across asset classes (MEDIUM risk, testable)

MacroAgent is a new component. It might produce shallow or incorrect
cross-asset reasoning.

**Mitigation:** Start with manual review of MacroAgent output for 2
weeks before letting it influence signals. Compare its cross-asset
impact assessments against actual market moves. The macro briefing
is generated once per run — easy to review daily.

### 6. Audience fragmentation (LOW risk, actual upside)

Crypto natives may not care about equity signals and vice versa.

**Why this is upside:** "AI finds the best swing trade across all markets"
is a more compelling product than "AI gives crypto signals." The content
story is richer. Discord channels per asset class let subscribers
self-select. The daily funnel report across all asset classes is
interesting content even on zero-signal days.

---

## What Does NOT Change

- **Philosophy:** Silence as default. Publish only when evidence is overwhelming.
- **Gate chain:** All 7+ gates remain. Thresholds remain strict.
- **LONG only, no leverage.** Applies to all asset classes.
- **Python owns all arithmetic.** LLMs interpret, never compute.
- **1d timeframe only.** Daily candles across all asset classes.
- **Deterministic trend scoring.** Same 7-signal indicator agreement score.
- **Duplicate-safe re-runs.** Same UNIQUE constraint logic.
- **Capitulation bypass logic.** Crypto F&G rising from extreme fear + RSI
  divergence. Applies to crypto only (equities use VIX, different thresholds).

---

## Product Positioning Shift

**Before (V3):** Crypto swing-trade signal service.
- Addressable audience: crypto-native traders
- Competition: hundreds of crypto signal bots, Telegram groups, X influencers
- Differentiation: transparent AI pipeline, gate-kill reporting

**After (V4):** Multi-asset AI analysis system.
- Addressable audience: anyone who swing-trades any liquid asset
- Competition: almost nobody doing transparent multi-asset LLM analysis
- Differentiation: "The AI that watches 40 markets so you don't have to"
- Content angle: daily macro briefing + per-asset analysis showing what
  passed and what didn't across all asset classes. "Today the AI rejected
  all crypto and all tech but found a high-conviction setup in gold."
  Interesting even on zero-signal days.

The brand "The Tenth Floor AI" is already asset-agnostic. The product
description shifts from "crypto signals" to "AI-powered swing trade
analysis across crypto, stocks, and commodities."

---

## Implementation Phases (high-level, not execution-planned)

### Phase 1: Foundation
- ~~Package rename: `crypto_swing_copilot` → `tenth_floor`~~ (complete)
- Restructure `universe.json` with `asset_class`, `class_leader`, `market_hours`
- Add `asset_class` column to SQLite schema (migration)
- `YFinanceDataFetcher` alongside existing `MarketDataFetcher`
- Parquet caching for equity/ETF/commodity OHLCV (same pattern as crypto)
- Market calendar integration (`exchange_calendars`)

### Phase 2: Sentiment & Macro Redesign
- MacroAgent: new agent that produces structured macro briefing
- Macro data fetchers: VIX, DXY, 10Y yield (all via yfinance/FRED)
- RSS feed expansion (8 feeds across crypto + financial news)
- Earnings calendar integration (via yfinance)
- Per-asset context injection into StrategyAgent prompts
- Remove current SentimentAgent, replace with MacroAgent + context layer

### Phase 3: Gate & Pipeline Generalization
- Gate 4: BTC relative strength → config-driven market-leader relative strength
- Gate 7: BTC correlation guard → config-driven class-leader correlation guard
- Hybrid signal cap: max per class + max per sector + max total
- Two-pass pipeline scheduling (equities pass + crypto pass)
- Conditional entry zones for equities (`CONDITIONAL_OPEN` + `VOID` status)
- Agent prompt updates: asset-class context injection, remove "crypto" language
- Outcome checker: per-class timeframe, market calendar, VOID handling

### Phase 4: Validation
- Backtest full multi-asset universe on 90 days of historical data
- Run equities in validation mode alongside crypto production for 30+ days
- Manual review of MacroAgent output quality for 2 weeks
- Compare signal quality metrics across asset classes
- Tune equity-specific thresholds if needed (via config profiles)

### Phase 5: Launch
- Publish multi-asset signals to Discord (per-asset-class channels)
- Update tweet drafter for multi-asset content
- Update dashboard with asset-class filters
- Update landing page and product description
- Social media content strategy for multi-asset narrative

---

## Design Principle: Build for V5 Without Building V5

V4 is a large expansion. Every new module should be written with the
assumption that V5 will add a web-based control plane and possibly forex.
This doesn't mean building abstractions for hypothetical futures — it
means keeping modules self-contained with clear interfaces so they can
be called from a web app or CLI equally.

Concretely:
- `run_pipeline()` stays a pure function that takes config and returns
  results. No interactive prompts, no terminal-only output.
- All config is file-driven (JSON/YAML), not hardcoded. A web UI can
  edit the same files.
- All state lives in SQLite. A web dashboard reads the same DB.
- MacroAgent, gate logic, and signal delivery are separate modules.
  A web UI can trigger them independently.

This is not extra work — it's just not painting yourself into a corner.

---

## Open Questions (to resolve before implementation)

1. **Forex — include or defer?** Forex adds another uncorrelated asset
   class but also adds complexity (24/5 market hours, pip-based pricing,
   leverage conventions). Recommendation: defer to V5. Crypto + equities
   + ETFs + commodities is already a significant expansion.

2. **LLM capacity:** 36 assets × 2 LLM calls each (Quant + Strategy)
   = 72 LLM calls per full run. Current 26 pairs are mostly pre-filtered
   (only 5-10 hit LLM). With uncorrelated assets, more will pass the
   trend pre-filter. Need to verify vLLM throughput on RTX 3090 can
   handle 40-50 LLM calls per run within acceptable time.

3. **Backtester scope:** Current backtester replays deterministic gates
   only (no LLM). For equity validation, do we need LLM-in-the-loop
   backtesting? This is expensive but would give real signal quality data.

4. **Dashboard redesign scope:** Minimal (add asset_class filter) or
   full redesign (separate views per class, macro dashboard, correlation
   matrix)? Recommend minimal for V4, full redesign for V5.

---

## V5 Vision (not planned, directional only)

**Web control plane.** Replace terminal-only pipeline execution with a
web UI that can:
- Trigger pipeline runs per asset class or full universe
- View live funnel reports and macro briefings
- Browse signal history with filters (asset class, date range, outcome)
- Edit universe and risk profile config visually
- Review and approve/reject signals before publication (human-in-the-loop)

**Why this matters:** Terminal execution is fine for one person running
daily. It's error-prone for a product — typos in commands, forgotten
flags, no audit trail of who ran what. A web UI also enables the
"subscriber dashboard" product tier (read-only access to signals,
macro briefings, and funnel reports).

**Also in V5 scope:**
- Forex asset class (if V4 validation shows the architecture handles it)
- Full dashboard redesign (correlation matrix, macro dashboard, per-class views)
- Subscriber-facing web portal (read-only signal feed, track record, funnel)
- Possible move from local vLLM to cloud LLM for reliability/scalability

V4 should be built so that V5 is a UI layer on top, not a rewrite.

---

## V3 Completion Criteria

V3 is complete when:
- [x] Pipeline funnel report posts to Discord every run
- [x] Backtester can replay 90 days and produce a gate-kill summary
- [x] LLM calls retry on transient failure (max_retries=3, timeout=30s)
- [x] All agent prompts reference 1d only, no stale 4h examples
- [x] `--profile` flag switches between validation/production configs
- [x] Pipeline failures post error embed to Discord
- [ ] At least one agent's prompt lives in Langfuse with hardcoded fallback
- [x] CI runs pytest + ruff + mypy on every push
- [x] DB migrations replace `CREATE TABLE IF NOT EXISTS`
