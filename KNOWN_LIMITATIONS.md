# Known Limitations

Documented but intentionally not fixed at this stage. Each entry is tagged in the relevant source file with `# KNOWN LIMITATION`.

---

## 1. ~~Entry Zone — ATR-Based Pullback~~ RESOLVED

Entry zones are now anchored to structural swing lows (support levels) detected by `TACalculator._detect_swing_levels()`. SL is placed below the nearest structural support with an ATR buffer. TP targets the nearest resistance above entry. ATR-based formula is only used as a fallback when no S/R levels are detected.

Resolved in commit `678544a`.

---

## 2. QuantAgent Confidence Score — Partially Addressed

**Source:** `agents/quant_agent.py` — LLM JSON output field `confidence`

The LLM-generated `confidence` field is still produced but is **no longer used for gating or conviction tiers**. A deterministic `trend_score` (0–1) computed from 7 binary indicator checks in `TACalculator._compute_trend_score()` now drives all pipeline decisions. The LLM confidence is only used as a fallback when `trend_score` is unavailable.

**Remaining gap:** Neither score is calibrated against historical win rates. Will be revisited at V2.1 with 30+ closed trades.

---

## 3. ~~R:R Is Formula-Derived, Not Structure-Based~~ RESOLVED

TP now targets the nearest resistance level (swing high) detected by `TACalculator._detect_swing_levels()`, clamped to a minimum R:R of 2.0. The pure formula-derived TP is only used as a fallback when no resistance levels are found above entry.

Resolved in commit `678544a`.

---

## 4. No Per-Pair Sentiment or Sector Differentiation

**Source:** `main.py` — `sentiment_signal` is computed once and shared across all 13 pairs.

All pairs receive the same F&G value and the same risk narrative. The system doesn't differentiate between BTC (macro-correlated), DeFi tokens (protocol-specific catalysts), or L1s (ecosystem activity). This contributes to homogenised rationales when market conditions are uniform.

**Future improvement (V2.2):** Add sector tags to `universe.json` and per-sector sentiment sources (e.g., BTC ETF flows, DeFi TVL trends, L1 developer activity).

---

## 5. Correlation-Based Risk Is Approximate

**Source:** `main.py` — BTC-correlation guard

The BTC-correlation guard (if BTC is rejected, cap alt signals to 2) is a heuristic, not a statistical correlation measure. It covers the most common failure mode (BTC dumps, alts follow) but doesn't account for varying correlation strengths between pairs.

**Future improvement (V2.2):** Compute rolling 30-day correlation matrix between universe pairs. Use it to limit cumulative exposure to highly-correlated clusters.
