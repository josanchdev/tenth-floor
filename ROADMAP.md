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
- The agent verdict logic is config-driven
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
| Agent verdict logic | Config-driven conviction tiers and confidence thresholds |
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
- **LONG only, no leverage.** Applies to all asset classes.
- **Python validates LLM output.** Sanity checks, R:R floor, distance bounds.
- **1d timeframe only.** Daily candles across all asset classes.
- **Duplicate-safe re-runs.** Same UNIQUE constraint logic.

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

### Phase 1: Foundation (complete)
- ~~Package rename: `crypto_swing_copilot` → `tenth_floor`~~
- ~~Restructure `universe.json` with `asset_class`, `class_leader`, `market_hours`~~
- ~~Add `asset_class` column to SQLite schema (migration)~~
- ~~`YFinanceDataFetcher` alongside existing `MarketDataFetcher`~~
- ~~Parquet caching for equity/ETF/commodity OHLCV (same pattern as crypto)~~
- ~~Generalize gates 4 & 7 from BTC-specific to class-leader~~
- ~~Multi-source routing in pipeline and outcome checker~~
- ~~Fix: honest R:R (no manufactured targets), symbol override, asset_class logging~~
- Market calendar integration (`exchange_calendars`) — deferred, not blocking

### Phase 1.5: AI-First Signal Generation (complete — 2026-04-07)

> **The architectural shift.** Moved from "Python decides, LLM rubber-stamps" to
> "LLM decides, Python validates." The LLM is the trader. Python is the data
> provider and safety net.

#### What was delivered

The full AI-first pipeline is live and verified with real market data. End-to-end
run on 2026-04-07 (F&G=11, VIX=25.53, risk-off): 36 assets scanned, 35 BUY
proposals, 7 caught by Python validation (R:R < 1.5), 28 reviewed by
RiskReviewer, 27 approved, 2 published (signal cap). Discord embeds and tweet
draft posted successfully.

Key decisions made during implementation:
- **Minimal prompts.** Agent prompts define role + output format only. No
  prescriptive trading rules, no "when to skip" checklists, no fixed factor
  lists. The LLM reasons freely with its own trading knowledge. Python validates
  the math. This was a deliberate shift after the initial prompts were too
  restrictive (34/36 assets skipped in risk-off because the prompt treated macro
  as a universal veto).
- **Asset class routing.** Each PairSnapshot carries its `asset_class` from
  universe.json. The MacroAnalyst's per-class outlook is matched by class, not
  by symbol string. TradeAnalyst sees the asset class label in its prompt.
- **Token budget discipline.** RTX 3090 supports 10240 total tokens with
  Qwen3-32B-AWQ. RiskReviewer uses compact per-proposal format (~2 lines each)
  to fit 30 proposals within budget. All agents verified to fit worst-case.
- **Graceful SKIP handling.** TradeProposal allows zero price fields for SKIPs
  (LLMs naturally return 0 when passing). Validation only runs on BUY proposals.

#### Why this phase exists

V3 used the LLM as a rubber stamp. Python computed all prices (entry, SL, TP),
a mechanical `trend_score` overrode the LLM's confidence, and RiskAgent's
accept/reject was pure Python rules. The agents were expensive window dressing —
the only real LLM decision was StrategyAgent's BUY/SKIP.

Modern reasoning models can do much more. Given rich context (indicators, macro,
structural levels, news), they can reason like a professional trader: identify
asymmetric setups, pick intelligent entry/SL/TP levels anchored to structure,
assess risk holistically across a portfolio. The pipeline should let them.

#### New architecture: 3 agents + validation layer

**Old flow:**
```
Python computes everything → LLM rubber-stamps → Python gates filter
```

**New flow:**
```
Data + Indicators (Python) → MacroAnalyst (1 LLM call) → context frame
                                                              ↓
Pre-screen (minimal, data-quality only) → ~30-36 candidates
                                                              ↓
TradeAnalyst (1 LLM call per candidate) → BUY proposals with entry/SL/TP/reasoning
                                                              ↓
Python validation (sanity checks) → valid proposals
                                                              ↓
RiskReviewer (1 LLM call, sees ALL proposals) → approved signals with conviction
                                                              ↓
Signal cap (hard business rule) → featured signals → publish
```

**Agent 1: MacroAnalyst** (runs once per pipeline)

The frame within which every trade is evaluated. Must run FIRST — its output
is the opening context of every TradeAnalyst call.

- Ingests: VIX level + trend, crypto F&G + trend, DXY level + trend, per-asset-class
  context, RSS headlines (Phase 2 enrichment), earnings calendar (Phase 2)
- Outputs structured JSON: macro regime (risk_on/risk_off/mixed/transitioning),
  per-asset-class impact brief, specific alerts (earnings, news catalysts)
- V1 (Phase 1.5): VIX + F&G + DXY only. Lightweight but gives the frame.
- V2 (Phase 2): Add RSS feeds, 10Y yield, earnings calendar, FRED data.

Replaces current SentimentAgent.

**Agent 2: TradeAnalyst** (runs per candidate asset — the core)

A professional swing trader with 20 years of experience. Given full technical
and macro context, decides if the trade is worth taking. If yes, specifies
the complete plan: entry, SL, TP, with reasoning for each level.

- Ingests: Full OHLCV history summary, all TA indicators, support/resistance
  levels, macro context from MacroAnalyst, asset-class context (market hours,
  entry type, overnight gap risk for equities)
- Outputs: BUY or SKIP. If BUY: entry zone, stop-loss, take-profit, confidence
  (0-1), reasoning for each price level, confluence factors, risk factors
- The LLM picks its own levels based on structural analysis. Python provides
  support/resistance as context, not as mandated prices.
- Prompt framing: "The macro environment today is [MacroAnalyst output]. Given
  this context, analyze [SYMBOL]. Here are the computed indicators and structural
  levels. Is this a swing trade worth taking? If yes, give me the full plan."

Replaces both QuantAgent and StrategyAgent. One coherent analysis with full
context is better than two fragmented calls.

**Agent 3: RiskReviewer** (runs once, sees ALL proposals together)

The chief risk officer. Reviews all proposals as a portfolio, not one at a time.

- Ingests: ALL BUY proposals from TradeAnalyst (with full reasoning), macro
  context, current open signals from DB (portfolio state)
- Outputs per proposal: APPROVE/REJECT/MODIFY, conviction tier (high/standard),
  risk notes, portfolio reasoning
- Reasons about: sector concentration, asset-class correlation, total exposure,
  macro alignment, which setups are strongest relative to each other
- The LLM decides conviction and approval holistically. "XOM and XLE are both
  energy longs — in this risk-off environment, pick the stronger setup, don't
  approve both."

Replaces RiskAgent and most mechanical gates (correlation guard, sector cap,
relative strength).

**Python validation layer** (safety net, not judgment)

Hard rules that should NEVER be violated regardless of LLM output:
- No SHORT (spot only — force to SKIP if LLM hallucinates a short)
- SL < entry < TP (basic directional sanity)
- SL not more than 15% below entry (prevents absurd stops)
- TP not more than 50% above entry (prevents fantasy targets)
- R:R >= 1.5 hard floor (business integrity — subscribers should never get a
  mathematically unfavorable trade, regardless of LLM reasoning)
- R:R math verification (recalculate from LLM's numbers, flag if wrong)
- Signal cap (max featured per day — business rule, not trade quality)
- Duplicate check (same asset already has an open signal)

If validation fails, signal is rejected with a clear log. These are "the LLM
made a math error or hallucinated" catches, not judgment calls.

#### What gets deleted

| Component | Fate | Why |
|-----------|------|-----|
| QuantAgent | Merged into TradeAnalyst | Trend classification is part of trade analysis |
| StrategyAgent | Merged into TradeAnalyst | One coherent analysis > two fragmented calls |
| SentimentAgent | Replaced by MacroAnalyst | Richer input, structured output, per-class reasoning |
| RiskAgent | Replaced by RiskReviewer | Portfolio-level LLM reasoning > per-signal Python rules |
| Gate 1 (trend regime) | Deleted | TradeAnalyst won't BUY a strong downtrend |
| Gate 2 (strategy SKIP) | Implicit | TradeAnalyst says BUY or SKIP directly |
| Gate 3 (volume) | Deleted | TradeAnalyst sees volume data and reasons about it |
| Gate 4 (relative strength) | Deleted | RiskReviewer handles cross-asset comparison |
| Gate 5 (confidence) | Deleted | TradeAnalyst's confidence is real, used by RiskReviewer |
| Gate 6 (R:R) | Moved to validation | Python verifies math, 1.5 hard floor |
| Gate 7 (correlation guard) | Moved to RiskReviewer | LLM reasons about correlation |
| Gate 8 (sector cap) | Moved to RiskReviewer | LLM reasons about diversification |
| `trend_score` | Deleted | LLM confidence is the confidence |
| `_compute_price_levels()` | Deleted | TradeAnalyst picks its own levels |
| Pre-filter (trend_score) | Replaced by pre-screen | Data-quality only, extremely permissive |

#### Pre-screen (minimal, data-quality only)

The pre-screen exists to save GPU time on degenerate cases, NOT to make
judgment calls. Kill only when:
- Fewer than 50 OHLCV bars (insufficient data for meaningful analysis)
- Zero volume for 5+ consecutive days (illiquid / stale data)
- Last 5 candles ALL > 5% red with no recovery candle (violent crash in
  progress — even a pro wouldn't catch a falling knife mid-freefall)

Everything else goes to TradeAnalyst. 36 assets at ~6 seconds each = 3.6
minutes. That's fine for a once-daily pipeline. The opportunity cost of
killing a capitulation reversal is higher than 3 extra minutes of compute.

#### R:R hard floor rationale

The 1.5 minimum R:R is not about trade quality — it's about business integrity.
If RiskReviewer approves a signal with 1.2 R:R and it loses, the subscriber
lost more than they could have gained. That's reputational damage regardless
of whether the LLM's reasoning was sound.

The LLM decides if the setup is good. Python confirms the math makes sense.
Those are two different jobs.

Asset-class aware in future: equity setups often have tighter but more reliable
ranges. The 1.5 floor may need per-class tuning after validation data accumulates.

#### Implementation order

1. MacroAnalyst v1 — lightweight macro context (VIX + F&G + DXY)
2. TradeAnalyst — merge Quant + Strategy, LLM picks levels, receives macro frame
3. Python validation layer — sanity checks on LLM output
4. RiskReviewer — portfolio-level LLM reasoning, sees all proposals + macro
5. Update Pydantic models — SetupProposal becomes LLM-determined
6. Update main.py — new pipeline flow, delete old gates
7. Update FunnelTracker — new stages for diagnostics
8. Validation runs — compare old vs new output quality

#### LLM call budget

**Phase 1.5 (3090, current):**
- 1 MacroAnalyst call
- ~30-36 TradeAnalyst calls
- 1 RiskReviewer call (batch — all proposals in one call)
- Total: ~32-38 calls per run, ~4-5 minutes on RTX 3090

**Phase 2 (5090, planned):**
- 1 MacroAnalyst call
- ~30-36 TradeAnalyst calls
- ~20-30 RiskReviewer calls (per-proposal with full context)
- Total: ~52-68 calls per run, ~8-10 minutes on RTX 5090
- Higher call count but dramatically better reasoning quality per call

---

### Phase 2: Signal Quality (5090 deployment)

> **The only thing that matters.** Signal quality is the product. Everything
> else — delivery, UI, business features — is packaging. Phase 2 is entirely
> about making every published signal one that a professional trader would
> respect and a subscriber would trust.

Phase 2 is structured in three sub-phases. 2A enables 2B (infrastructure
unlocks the token budget for richer context). 2C is independent and can
be done in parallel.

#### Phase 2A: Infrastructure + Reasoning Quality

Deploy to 5090 and fix the structural quality issues identified during
Phase 1.5 validation runs.

- **Docker Compose deployment** — vLLM + pipeline as services, reproducible
  across machines. `.env.3090` / `.env.5090` hardware profiles controlling
  context length, GPU utilization, model selection, and token budgets.
- **Per-proposal RiskReviewer** — Replace batch review (compressed 2-line
  summaries, 96% approval rate) with individual per-proposal calls. Each
  call receives: full trade thesis, macro frame, portfolio state (open
  signals + already-approved signals today). The CRO can actually reason:
  "LINK at RSI 46 in risk-off is weak. GLD is the right play here. REJECT."
- **TradeAnalyst prompt refinement** — Remove "a separate system validates
  your math" (creates moral hazard — LLM outsources R:R to Python). Add
  business context: "You serve paying subscribers. Your reputation depends
  on every signal. Silence is preferred over a mediocre setup."
- **Macro-aware signal ranking** — When the signal cap activates, rank by
  macro alignment, not just raw confidence. In risk-off: safe-haven signals
  outrank risk-asset signals at equal confidence.
- **Model evaluation** — Test Qwen 3.5 (or latest available) on the 5090.
  32GB VRAM may support unquantized models or larger context windows.
  Better reasoning = better signal quality with the same minimal prompts.

#### Phase 2B: Context Enrichment

Give the LLM the information a real trader reads every morning. This is
the #1 quality gap — the MacroAnalyst currently has 3 data points (VIX,
F&G, DXY) and no idea *why* the market is moving.

- **RSS feed integration** — 8 feeds across crypto + financial news.
  MacroAnalyst v2 receives full headlines + summaries, not just F&G.
  Enables reasoning like "tariff-driven sell-off hurts tech but is
  neutral to gold" instead of generic "risk-off."
- **10Y yield via FRED** — Bond yield context for treasury/rate-sensitive
  plays. Cheap to add, valuable for TLT and financials reasoning.
- **Earnings calendar via yfinance** — Per-asset alerts injected into
  TradeAnalyst prompt: "AAPL earnings in 3 days — elevated vol risk."
  The per-proposal RiskReviewer can flag: "reject, earnings risk."
- **Asset-specific news injection** — TradeAnalyst receives news relevant
  to its specific asset, not just class-level macro. Requires the 5090
  token budget to include this without compressing everything else.

#### Phase 2C: Equities-Specific

Independent of 2A/2B. Can be done in parallel.

- **Conditional entry zones** — `CONDITIONAL_OPEN` status for equities
  that need market-hours confirmation. `VOID` if conditions change.
- **Two-pass pipeline scheduling** — Equities pass (pre-market or at open)
  + crypto pass (any time). RiskReviewer on second pass sees first-pass
  approvals in portfolio state.
- **Outcome checker: market calendar** — Skip weekends/holidays for
  equities. `exchange_calendars` integration. VOID handling.

### Phase 3: Validation + Track Record

Run the Phase 2 architecture for 30+ days to build a verifiable track record
before launch. Signal quality must be proven, not assumed.

- Run pipeline daily, log all signals and outcomes to SQLite
- Manual review: would a pro trader agree with each signal?
- Performance metrics: hit rate, average R:R achieved, win/loss by class
- Compare risk-off vs risk-on signal quality (the Phase 1.5 weakness)
- Backtest full universe on 90 days of historical data
- Tune per-asset-class R:R floors if data supports it
- A/B: Phase 1.5 prompts vs Phase 2 prompts on same day's data

### Phase 4: Launch + Delivery

Only after Phase 3 proves signal quality. These are packaging and
distribution — they don't affect the core product.

**Core launch:**
- Publish multi-asset signals to Discord (per-asset-class channels)
- Update tweet drafter for multi-asset content
- Update dashboard with asset-class filters and performance tracking
- Public track record page (verified P&L, transparency builds trust)
- Landing page and social media strategy for multi-asset narrative

**Delivery improvements (post-launch, prioritise by subscriber feedback):**
- Discord bot (interactive: /explain, /portfolio, /why-skip commands)
- Telegram channel (broader reach, better mobile notifications)
- Subscriber web dashboard (signal history, portfolio builder, alerts)
- Email digest (weekly recap with win/loss stats)
- Tiered subscriptions (free: delayed signals, paid: real-time + thesis)

**Future (Phase 5+, not planned):**
- API access for power users
- Web-based control plane for pipeline management
- Forex expansion
- Multi-language support
- Backtested performance reports for marketing

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
- Agents, validation, and signal delivery are separate modules.
  A web UI can trigger them independently.

This is not extra work — it's just not painting yourself into a corner.

---

## Open Questions (to resolve before implementation)

1. **Forex — include or defer?** Forex adds another uncorrelated asset
   class but also adds complexity (24/5 market hours, pip-based pricing,
   leverage conventions). Recommendation: defer to V5. Crypto + equities
   + ETFs + commodities is already a significant expansion.

2. **LLM capacity:** 1 MacroAnalyst + ~36 TradeAnalyst + 1 RiskReviewer
   = ~38 LLM calls per full run. At ~6 seconds per call on RTX 3090,
   total pipeline time is ~4-5 minutes. Acceptable for a daily run.

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
