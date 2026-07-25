# Dashboard Design System

The Tenth Floor — personal research dashboard. Locked 2026-04-11.

This document is the source of truth for visual tokens, motion rules, and component primitives. React components consume these tokens via Tailwind v4 `@theme` / CSS variables. Never introduce colors, sizes, or motion values inline — extend the tokens here first.

---

## Philosophy

Three rules, in order of priority:

1. **Daily-use friction is compound interest.** I open this dashboard once a day for a year. Every point of friction or aesthetic fatigue compounds. Design for the 365th visit, not the first.
2. **Silence is the default.** The pipeline often produces zero signals. Empty states are the most important states. Design them with intention.
3. **Glow = state transition, never decoration.** Motion is how information arrives on screen. If an animation doesn't represent a state change, delete it.

Reference tier: Linear, Vercel, Stripe dashboard. Anti-reference: Bootstrap admin templates, AI-generated React boilerplate, generic dark SaaS dashboards.

---

## Color tokens

All colors are dark-mode only. No light mode, no theme toggle.

### Base palette

| Token | Value | Usage |
|---|---|---|
| `--bg` | `#0A0712` | Page background (deep indigo-near-black) |
| `--surface` | `rgba(255,255,255,0.03)` | Cards, modals, drawers |
| `--surface-raised` | `rgba(255,255,255,0.05)` | Hovered cards, active modal |
| `--border` | `rgba(255,255,255,0.06)` | All default borders |
| `--border-hover` | `rgba(255,255,255,0.12)` | Hovered card borders |

### Text cascades

| Token | Value | Usage |
|---|---|---|
| `--text` | `#F0F4FF` | Primary text, headlines, numbers |
| `--text-70` | `rgba(240,244,255,0.70)` | Body text |
| `--text-50` | `rgba(240,244,255,0.50)` | Secondary, meta, mono labels |
| `--text-40` | `rgba(240,244,255,0.40)` | Tertiary, placeholder, axes |
| `--text-30` | `rgba(240,244,255,0.30)` | Quaternary, footer, disabled |

### Purple (primary accent)

| Token | Value | Usage |
|---|---|---|
| `--purple` | `#B47BFF` | Primary accent — borders on approved, Run button base |
| `--purple-light` | `#D4B3FF` | KPI deltas, brand gradient mid, muted interactive |
| `--purple-deep` | `#7C3AED` | Run button gradient end, brand gradient end |
| `--purple-soft` | `rgba(180,123,255,0.35)` | Soft borders, glow diffuse |
| `--purple-glow` | `rgba(180,123,255,0.22)` | Box-shadow glow color |

### Teal (restricted accent)

| Token | Value | Usage |
|---|---|---|
| `--teal` | `#5EEAD4` | **ONLY the `analyzing` asset card state. Nowhere else.** |
| `--teal-soft` | `rgba(94,234,212,0.35)` | Analyzing card border |

This restriction is load-bearing. Teal is the "this is happening right now, in real time" signal. Diluting it to decorative use destroys that meaning.

### Semantic (state)

| Token | Value | Usage |
|---|---|---|
| `--win` | `#B47BFF` | HIT_TP signals in archive (same as purple — purple = good) |
| `--loss` | `#FF7B9C` | HIT_SL signals in archive (muted pink, not harsh red) |
| `--open` | `#5EEAD4` | OPEN signals (reuse teal — this is the one exception to the teal rule, because "open" IS a live state) |
| `--expired` | `rgba(240,244,255,0.30)` | EXPIRED signals (just faded) |

### Brand gradient

```css
linear-gradient(135deg, #F0F4FF 0%, #D4B3FF 40%, #B47BFF 75%, #7C3AED 100%)
```

Reserved for: expectancy hero number, the primary KPI value, approved card price, section section dividers on scroll. **One gradient per viewport section, max.** If you use it twice in a screen, it stops being special.

---

## Typography

Fonts loaded via Google Fonts. No self-hosting until we productionize.

```
Geist       — 300, 400, 500, 600, 700
Geist Mono  — 400, 500
```

All numeric displays use `font-feature-settings: "tnum"` (tabular figures) so digits don't jitter when values update.

### Scale

| Token | Font | Size | Weight | Line | Letter | Usage |
|---|---|---|---|---|---|---|
| `--t-display` | Geist | 72px | 300 | 1.0 | -0.03em | Hero expectancy number |
| `--t-h1` | Geist | 40px | 400 | 1.1 | -0.02em | View titles |
| `--t-h2` | Geist | 24px | 500 | 1.2 | -0.01em | Section headers |
| `--t-h3` | Geist | 18px | 500 | 1.3 | -0.005em | Card titles |
| `--t-body` | Geist | 15px | 400 | 1.6 | 0 | Paragraph text |
| `--t-small` | Geist | 12px | 400 | 1.5 | 0 | Meta, captions |
| `--t-label` | Geist Mono | 11px | 500 | 1 | 0.14em | Section labels, KPI labels, eyebrows — ALWAYS UPPERCASE |
| `--t-mono` | Geist Mono | 13px | 400 | 1.5 | 0.02em | Code, run_id, timestamps, symbols |
| `--t-kpi` | Geist | 32px | 400 | 1 | -0.02em | KPI tile values |

Rule: headlines get negative letter-spacing, small text stays at 0, labels get positive letter-spacing (0.14em) and uppercase.

---

## Spacing & radius

Tailwind v4 default spacing (4px base). Custom radius tokens:

| Token | Value | Usage |
|---|---|---|
| `--radius-card` | 12px | Cards, modals, drawers |
| `--radius-chip` | 8px | Compact session-signal chips, status badges |
| `--radius-full` | 9999px | Pill buttons, avatar circles |

Section vertical rhythm: `96px` between top-level sections, `48px` between subsections, `24px` between related children.

---

## Motion

### Principles

1. **Glow = state transition, never decoration.** If an animation doesn't represent a state change, delete it.
2. **The WebSocket is the choreographer.** Every asset card state change is an event from the backend, and the event IS the animation trigger. No polling, no setInterval fakery.
3. **Numbers tween on arrival.** KPI values animate from 0 → value on first render (800ms, eased). Subsequent updates tween from old → new (400ms, eased).
4. **Tabular figures.** No digit jitter. Ever.

### Easing

```
--ease-out:   cubic-bezier(0.2, 0.8, 0.2, 1)    — default for all transitions
--ease-in:    cubic-bezier(0.6, 0.0, 0.8, 0.2)  — exits only
--ease-spring: cubic-bezier(0.5, 1.5, 0.5, 1)   — attention moments (approved pulse)
```

### Durations

| Token | Value | Usage |
|---|---|---|
| `--dur-fast` | 150ms | Hover states, focus rings |
| `--dur-base` | 250ms | Most transitions |
| `--dur-slow` | 400ms | Modal summon, drawer slide, card state change |
| `--dur-number` | 800ms | KPI count-up on first load |
| `--dur-chart` | 1200ms | Path-length chart draw on scroll-into-view |

### Approved exceptions to "no decoration"

Two elements get idle animation because they're load-bearing CTAs or state indicators:

- **Run button** — subtle breathing glow (purple box-shadow opacity 0.3 → 0.5, 3s cycle). It's the single CTA on the landing page; it needs to pull the eye.
- **Analyzing asset card** — teal border pulse (opacity 0.15 → 0.35, 2.4s cycle) + 2s shimmer line sweep. This is a live-state indicator, not decoration.

Nothing else loops. Period.

---

## Background stack

From back to front:

1. **Base:** solid `--bg` (`#0A0712`)
2. **Atmosphere:** single radial gradient, top-right only — `radial-gradient(ellipse 50% 40% at 90% 0%, rgba(180,123,255,0.06), transparent 65%)`. Static, not animated.
3. **Noise overlay:** 0.025 opacity fractal SVG, `mix-blend-mode: overlay`. Breaks up gradient banding and kills the "AI-generated" sterile feel. This is the #1 anti-AI trick — keep it.

No grid, no starfield, no breathing gradients, no floating particles, no parallax.

---

## Component primitives

### Card (base)

```
background: var(--surface)
border: 1px solid var(--border)
border-radius: var(--radius-card)
padding: 24px
backdrop-filter: blur(12px)
```

Hover: `border-color` → `var(--border-hover)`, `background` → `var(--surface-raised)`, duration `--dur-fast`.

### KPI tile

Card primitive, plus:
- `--t-label` label at top, `--t-kpi` value below, delta line below that
- Value gets `.accent` modifier for the primary KPI (expectancy) — applies brand gradient text
- Delta uses `--purple-light` for positive, `#FF7B9C` for negative
- Count-up animation on mount (800ms)

### Run button

```
background: linear-gradient(135deg, var(--purple) 0%, var(--purple-deep) 100%)
border: none
border-radius: var(--radius-card)
padding: 18px 32px
font: 500 13px/1 Geist, uppercase, letter-spacing 0.08em
box-shadow: 0 0 0 1px rgba(180,123,255,0.5), 0 12px 48px var(--purple-glow)
```

Hover: shadow intensifies, `translateY(-1px)`.
Idle: 3s breathing glow (see Motion above).
Disabled state (during a run): opacity 0.4, no glow, cursor not-allowed, label changes to "Running…"

### Asset card (state machine)

Single component with 5 states. Shares `layoutId` across runner modal columns so Framer Motion animates physical position changes.

| State | Border | Background | Effect |
|---|---|---|---|
| `queued` | `--border` | `--surface` | Text at `--text-40` |
| `analyzing` | `--teal-soft` | `--surface` | Teal border pulse + shimmer line (2s sweep) |
| `proposal` | `--purple-soft` | `--surface` | State label in `--purple` |
| `approved` | `--purple` | Purple gradient fill `rgba(180,123,255,0.08)` → `rgba(180,123,255,0.02)` | Strong purple glow `0 8px 48px var(--purple-glow)`, price uses brand gradient |
| `rejected` | `--border` | `--surface` | Opacity 0.3, price `text-decoration: line-through` |

State transitions use `--dur-slow` (400ms) with `--ease-out`.

### Modal (runner)

```
position: fixed; inset: 0
background: rgba(10,7,18,0.92)
backdrop-filter: blur(20px)
z-index: 100
```

Summon: 400ms fade + scale from 0.96 → 1, backdrop blurs 0 → 20px.
Dismiss: 250ms fade out + scale 1 → 0.98, backdrop blur 20 → 0.
ESC key force-closes. Auto-closes on `run_complete` after 2s victory beat.

Content layout:
- Top: phase rail (Macro → Analyze → Review → Publish), sweeps left-to-right
- Center: asset card grid (CSS grid, responsive, ~220px min card width)
- Bottom: collapsible log console, `--t-mono` font, default collapsed

### Drawer (signal detail)

```
position: fixed; right: 0; top: 0; bottom: 0
width: min(560px, 80vw)
background: var(--surface-raised)
border-left: 1px solid var(--border)
z-index: 90
```

Summon: slides in from right, 400ms `--ease-out`.
Dismiss: slides out, ESC or click backdrop or click X.

### Status chip

```
font: 500 10px/1 Geist Mono, uppercase, letter-spacing 0.14em
padding: 4px 10px
border-radius: var(--radius-full)
border: 1px solid [semantic color at 0.35 alpha]
color: [semantic color]
```

Variants: PUBLISHED, SESSION, OPEN, HIT_TP, HIT_SL, EXPIRED.

### Section header

```
display: flex; align-items: center; gap: 12px
font: --t-label
color: --text-40
margin-bottom: 24px
```

After the label text, a 1px `--border` line fills the remaining horizontal space. This is the repeating section divider pattern across all views.

---

## States (empty / loading / error)

### Empty states

Designed, not afterthoughts. Every section has one. Templates:

| Section | Empty copy |
|---|---|
| Track Record / Today's signals | "No signals today — silence is the default." |
| Archive (filtered) | "No signals match these filters. Try widening the date range." |
| Expectancy Lab (n=0) | "No resolved signals yet. The scoreboard lights up after the first outcome." |
| Runner (cold open) | "Pipeline idle. Hit Run when you're ready." |

Empty states use `--text-50` for the copy, centered in the container, with a single `--text-30` icon above (optional).

### Loading (skeleton)

Skeleton blocks match real component dimensions exactly. Background: `rgba(255,255,255,0.04)`. Shimmer: left-to-right sweep, 1.4s loop. Use for initial data fetches (signals list, stats). Do NOT use for live events — those come via WebSocket and have their own state machine.

### Error

Red-free. Use `#FF7B9C` only for loss/negative deltas — errors get a card with `--text-70` copy and a "Retry" button using the chip style. Error copy is specific, never "Something went wrong":
- "Couldn't reach the API at 127.0.0.1:8000 — is the backend running?"
- "WebSocket disconnected — click to reconnect."

---

## Views

### View 1 — Track Record (landing)

Vertical scroll, no inner scroll containers. Sections top to bottom:

1. **Hero** — date eyebrow, "Today's research" brand-gradient title, idle/running status line with pulsing purple dot, Run button (right-aligned)
2. **Portfolio snapshot** — 4-tile KPI grid (Expectancy / Win rate / Avg R:R / Open positions). Expectancy uses `.accent` gradient.
3. **Today's signals** — three tiers:
   - Published (top 5) — full asset cards
   - Session signals — compact chip row with "expires in Xh" label
   - Skipped — collapsed by default, expand to audit
4. **Equity curve** — Visx LineChart, cumulative R over time, draws on scroll-into-view. Concentration band overlay.

### View 2 — Signal Archive

1. **Sticky filter bar** — asset, status, asset class, date range (default 30d), conviction
2. **Card stack** — vertical list, one signal per row ~120px tall, newest first
3. **Drawer** — slides in from right on row click, shows full LLM rationale, MAE/MFE, price action since publish, notes field

### Runner — modal (not a view)

Covered under Component primitives → Modal above.

---

## What this document does NOT cover

- Specific React component APIs — that's `dashboard/src/components/` once scaffolded
- State management (Zustand? Context? Signal library?) — decide during scaffolding
- Data fetching strategy (TanStack Query? SWR? native fetch?) — decide during scaffolding
- Testing approach — decide during scaffolding

Those are implementation decisions. This doc locks what the thing looks and feels like.

---

*Last updated: 2026-04-11*
*Next review: after first render of Track Record view*
