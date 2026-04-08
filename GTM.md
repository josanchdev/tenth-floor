# The Tenth Floor AI — Go-to-Market Plan

> Created: 2026-03-28. Updated: 2026-03-30 (V4 multi-asset pivot).
> Living document — update as you progress.
> Reference: [research.md](research.md) for competitor analysis, pricing research, and legal notes.

---

## The Product

**The Tenth Floor AI** is an AI-powered multi-asset swing-trade analysis service.

A multi-agent LLM pipeline analyzes ~36 assets daily — crypto, US equities, ETFs,
and commodities — through 7 sequential filters. It publishes only when the technical
evidence is overwhelming — silence is the default. Max 3 featured signals per day.
No leverage, no shorts, spot only.

### What makes it different

- **AI, not a person.** Transparent, auditable, no ego. The pipeline either passes
  the gates or it doesn't.
- **Multi-asset coverage.** Crypto, stocks, ETFs, commodities — structurally
  uncorrelated assets so the pipeline finds opportunities in any market regime.
  "The AI rejected all crypto today but found a setup in gold."
- **3-agent AI pipeline.** MacroAnalyst sets the regime → TradeAnalyst proposes
  per-asset trades with LLM-chosen entry/SL/TP → RiskReviewer approves at the
  portfolio level. Python validates the math (R:R >= 1.5, sane levels) and
  enforces hard rules. Most days, very few setups pass. That's the point.
- **Full accountability.** Every signal is logged with entry, SL, TP, and tracked to
  outcome (TP hit, SL hit, or expired). Wins and losses published equally.
- **Silence is a signal.** "We analyzed 36 markets and found 1 worth acting on" is
  more valuable than 10 low-conviction trades.
- **Market-price entries.** Crypto subscribers can act immediately. Equity signals
  include conditional entry zones (valid only if next-day open is within range).

### Pricing (planned)

- Founding members: EUR 79/mo (locked forever)
- Standard: EUR 99/mo (after 50 members)
- Annual: EUR 799/year (save EUR 149)
- No free trial — free Discord + public track record IS the trial

### Legal framing

Frame as **"AI market analysis tool"**, not "trading signals." Same content to all
subscribers, no individual risk assessment, no personalized recommendations.
See [research.md](research.md) for full MiCA analysis.

---

## The Funnel

```
X/Twitter (build in public — process, not signals)
    |
Landing page (philosophy, how the AI works, track record, email signup)
    |
Free Discord (delayed signals, public track record, community)
    |
Paid Discord via Whop (real-time signals, full reasoning, outcomes)
```

---

## Phases

### Phase 1 — Foundation (now → 30 closed trades)

You are here. The pipeline is running in validation mode. No product to sell yet.
The goal is to build the track record and start an organic presence.

- [x] Pipeline running daily (`./run.sh --profile validation`)
- [x] Outcome checker resolving signals (`check_outcomes`)
- [ ] V4 multi-asset expansion (see [ROADMAP.md](ROADMAP.md))
- [ ] Create X account (handle, bio, profile pic/banner — don't post yet)
- [ ] Landing page v1 — "coming soon" with:
  - What The Tenth Floor AI is (multi-asset, not crypto-only)
  - The 3-agent AI philosophy ("why we say nothing most days")
  - How the AI works (pipeline diagram, not technical jargon)
  - Email signup ("get notified when we launch")
  - No pricing yet
- [ ] Start posting on X (after ~1 week of pipeline runs):
  - **Process content only** — no signals, no trades, no "buy this"
  - Funnel screenshots ("36 markets analyzed, 2 approved — here's why")
  - Philosophy posts ("why silence is our most common recommendation")
  - "How the AI thinks" threads (agents, indicators, R:R)
  - Multi-asset angle ("today the AI rejected all crypto but found gold")
  - 3-4 posts per week (you're a student with a job, not a content machine)
- [x] Build auto-poster script (pipeline output → LLM drafts tweet → you review)

#### X content ideas for Phase 1

These require zero track record — they're about the process:

1. "Built an AI that analyzes 36 markets — crypto, stocks, gold, bonds — through
   a 3-agent pipeline (macro → trade → risk review). Today it rejected everything
   except one gold setup. Here's the funnel." [screenshot]
2. "My AI has a minimum R:R of 1.5. Here's what that means and why most setups
   fail it."
3. "When crypto is in a downtrend, my AI doesn't force trades. It looks at gold,
   bonds, and defensive stocks instead."
4. "Most signal services spam 10 trades a day. Mine is designed to say nothing
   unless the evidence is overwhelming."
5. "Three AI agents review every setup. Today the macro agent flagged risk-off
   conditions and the trade agent skipped 32 of 36 assets on its own." [funnel
   visualization]
6. "Why I made my AI long-only, spot-only, no leverage. Thread."
7. "My AI's most important feature: it tells me when NOT to trade."
8. "36 assets. 4 markets. 3 AI agents. Max 3 signals per day. Most days: one or
   zero. That's the product."

### Phase 2 — Soft Launch (30+ closed trades)

The track record exists. Time to show it and start selling.

- [ ] Post full track record on X (wins AND losses, verifiable timestamps)
- [ ] Update landing page:
  - Add track record section (win rate, average R:R, total signals per asset class)
  - Add pricing
  - Add Whop join link
- [ ] Set up Whop (billing + Discord access management)
- [ ] Open free Discord tier:
  - Delayed signals (24h after paid members)
  - Public track record display
  - Read-only community access
- [ ] Open paid Discord tier (EUR 79/mo founding members)
- [ ] Cross-post outcomes to X (every TP hit, every SL hit — full accountability)

### Phase 3 — Growth (month 3-6 after launch)

- [ ] Auto-poster v2 — more sophisticated content from pipeline + outcomes data
- [ ] Weekly performance recap threads (automated from SQLite, per asset class)
- [ ] YouTube channel — weekly market analysis, pipeline explainers
- [ ] Affiliate program (20-30% recurring for referrals)
- [ ] Chart images with matplotlib/plotly for X posts (2-3x engagement)
- [ ] Telegram channel (mirror signals for broader reach)

### Phase 4 — Scale (month 6+)

- [ ] Raise price to EUR 99/mo for new members
- [ ] Annual plan (EUR 799/year)
- [ ] Video content ("My AI's top 5 trades this month" — once track record supports it)
- [ ] Consider AI-generated video pipeline (only after manual content proves the format)
- [ ] Higher tiers (portfolio-level analysis)

---

## Key Principles

1. **Track record before marketing.** No amount of content converts without proof.
2. **Process before product.** Show how the AI thinks, not what it recommends.
3. **Honesty over hype.** Post losses. Show zero-signal days. This IS the differentiator.
4. **Automate what you can, verify what you post.** AI drafts, you approve.
5. **Don't build what you don't need yet.** Video generation is Phase 4, not Phase 1.
6. **Multi-asset is the story.** The content angle is richer when the AI can compare
   across markets. Lean into cross-asset narratives.

---

## Technical Assets (this repo)

| Asset | Status | Used for |
|-------|--------|----------|
| Pipeline (`main.py`) | Running | Daily signal generation |
| Outcome checker (`check_outcomes.py`) | Running | Signal resolution |
| Dashboard (`dashboard/app.py`) | Built | Internal monitoring |
| Discord notifier | Built | Signal + funnel delivery |
| Backtester | Built | Threshold tuning |
| Auto-poster script (`post_tweet.py`) | Built | X content from pipeline output |
| Landing page (the-tenth-floor-site) | Needs work | Acquisition + conversion |

---

## Metrics to Track

### Phase 1 (now)
- Pipeline runs per week (target: 7/7)
- Signals generated (accumulating toward 30 closed)
- X followers (baseline, don't optimize yet)

### Phase 2 (soft launch)
- Win rate (target: display honestly, whatever it is)
- Win rate per asset class
- X engagement per post
- Email signups → Discord joins → paid conversions
- Monthly recurring revenue (MRR)
- Churn rate

### Phase 3+ (growth)
- Customer acquisition cost (CAC)
- Lifetime value (LTV) — target LTV:CAC > 3:1
- Referral rate
