# The Tenth Floor AI — Go-to-Market Plan

> Created: 2026-03-28. Living document — update as you progress.
> Reference: [research.md](research.md) for competitor analysis, pricing research, and legal notes.

---

## The Product

**The Tenth Floor AI** is an AI-powered crypto swing-trade analysis service.

A multi-agent LLM pipeline analyzes 26 crypto pairs daily through 7 sequential
filters. It publishes only when the technical evidence is overwhelming — silence
is the default. Max 2 signals per day. No leverage, no shorts, spot only.

### What makes it different

- **AI, not a person.** Transparent, auditable, no ego. The pipeline either passes
  the gates or it doesn't.
- **7-gate filter system.** Trend regime → strategy → volume → BTC relative strength
  → confidence → R:R ≥ 2.0 → sector diversity. Most days, nothing passes. That's
  the point.
- **Full accountability.** Every signal is logged with entry, SL, TP, and tracked to
  outcome (TP hit, SL hit, or expired). Wins and losses published equally.
- **Silence is a signal.** "We analyzed 26 pairs and bought nothing" is more valuable
  than 10 low-conviction trades.
- **Market-price entries.** Subscribers can act immediately — no waiting for a pullback
  that may never come.

### Pricing (planned)

- Founding members: €79/mo (locked forever)
- Standard: €99/mo (after 50 members)
- Annual: €799/year (save €149)
- No free trial — free Discord + public track record IS the trial

### Legal framing

Frame as **"AI market analysis tool"**, not "trading signals." Same content to all
subscribers, no individual risk assessment, no personalized recommendations.
See [research.md § 7](research.md) for full MiCA analysis.

---

## The Funnel

```
X/Twitter (build in public — process, not signals)
    ↓
Landing page (philosophy, how the AI works, track record, email signup)
    ↓
Free Discord (delayed signals, public track record, community)
    ↓
Paid Discord via Whop (real-time signals, full reasoning, outcomes)
```

---

## Phases

### Phase 1 — Foundation (now → 30 closed trades)

You are here. The pipeline is running in validation mode. No product to sell yet.
The goal is to build the track record and start an organic presence.

- [x] Pipeline running daily (`./run.sh --profile validation`)
- [x] Outcome checker resolving signals (`check_outcomes`)
- [ ] Create X account (handle, bio, profile pic/banner — don't post yet)
- [ ] Landing page v1 — "coming soon" with:
  - What The Tenth Floor AI is
  - The 7-gate philosophy ("why we say nothing most days")
  - How the AI works (pipeline diagram, not technical jargon)
  - Email signup ("get notified when we launch")
  - No pricing yet
- [ ] Start posting on X (after ~1 week of pipeline runs):
  - **Process content only** — no signals, no trades, no "buy this"
  - Funnel screenshots ("26 pairs analyzed, 0 approved — here's why")
  - Philosophy posts ("why silence is our most common recommendation")
  - "How the AI thinks" threads (gates, indicators, R:R)
  - 3–4 posts per week (you're a student with a job, not a content machine)
- [ ] Build auto-poster script (pipeline output → LLM drafts tweet → you review)

#### X content ideas for Phase 1

These require zero track record — they're about the process:

1. "Built an AI that analyzes 26 crypto pairs through 7 filters. Today it rejected
   all of them. Here's the funnel." [screenshot]
2. "My AI has a minimum R:R of 2.0. Here's what that means and why most setups
   fail it."
3. "Fear & Greed hit 13 today. Here's how my trading AI responds to extreme fear."
4. "Most signal services spam 10 trades a day. Mine is designed to say nothing
   unless the evidence is overwhelming."
5. "I built a 7-gate filter for crypto signals. Gate 1 alone killed 19 out of 26
   pairs today." [funnel visualization]
6. "Why I made my AI long-only, spot-only, no leverage. Thread 🧵"
7. "My AI's most important feature: it tells me when NOT to trade."
8. "26 pairs. 7 filters. Max 2 signals per day. Most days: zero. That's the product."

### Phase 2 — Soft Launch (30+ closed trades)

The track record exists. Time to show it and start selling.

- [ ] Post full track record on X (wins AND losses, verifiable timestamps)
- [ ] Update landing page:
  - Add track record section (win rate, average R:R, total signals)
  - Add pricing
  - Add Whop join link
- [ ] Set up Whop (billing + Discord access management)
- [ ] Open free Discord tier:
  - Delayed signals (24h after paid members)
  - Public track record display
  - Read-only community access
- [ ] Open paid Discord tier (€79/mo founding members)
- [ ] Cross-post outcomes to X (every TP hit, every SL hit — full accountability)

### Phase 3 — Growth (month 3–6 after launch)

- [ ] Auto-poster v2 — more sophisticated content from pipeline + outcomes data
- [ ] Weekly performance recap threads (automated from SQLite)
- [ ] YouTube channel — weekly market analysis, pipeline explainers
- [ ] Affiliate program (20–30% recurring for referrals)
- [ ] Chart images with matplotlib/plotly for X posts (2–3x engagement)
- [ ] Telegram channel (mirror signals for broader reach)

### Phase 4 — Scale (month 6+)

- [ ] Raise price to €99/mo for new members
- [ ] Annual plan (€799/year)
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

---

## Technical Assets (this repo)

| Asset | Status | Used for |
|-------|--------|----------|
| Pipeline (`main.py`) | ✅ Running | Daily signal generation |
| Outcome checker (`check_outcomes.py`) | ✅ Running | Signal resolution |
| Dashboard (`dashboard/app.py`) | ✅ Built | Internal monitoring |
| Discord notifier | ✅ Built | Signal + funnel delivery |
| Backtester | ✅ Built | Threshold tuning |
| Auto-poster script | ❌ Not built | X content from pipeline output |
| Landing page (the-tenth-floor-site) | 🔨 Needs work | Acquisition + conversion |

---

## Metrics to Track

### Phase 1 (now)
- Pipeline runs per week (target: 7/7)
- Signals generated (accumulating toward 30 closed)
- X followers (baseline, don't optimize yet)

### Phase 2 (soft launch)
- Win rate (target: display honestly, whatever it is)
- X engagement per post
- Email signups → Discord joins → paid conversions
- Monthly recurring revenue (MRR)
- Churn rate

### Phase 3+ (growth)
- Customer acquisition cost (CAC)
- Lifetime value (LTV) — target LTV:CAC > 3:1
- Referral rate
