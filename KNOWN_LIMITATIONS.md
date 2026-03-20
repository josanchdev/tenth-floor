# Known Limitations

Documented but intentionally not fixed at this stage. Each entry is tagged in the relevant source file with `# KNOWN LIMITATION`.

---

## 1. Entry Zone Uses ±0.5% of Spot Price

**Source:** `agents/strategy_agent.py` — `_compute_price_levels()`

```python
entry_low  = price * 0.995   # KNOWN LIMITATION
entry_high = price * 1.005
```

The entry zone is a symmetric ±0.5% band around the current spot price. This has no relationship to market structure.

A correct implementation derives the entry zone from ATR-based support and resistance levels — the nearest structural level below (for longs) defines the lower bound of a meaningful entry zone.

**Impact:** Entry zones are too wide and centred on an arbitrary price rather than a technically significant level. The R:R ratio calculation is affected as a result.

---

## 2. QuantAgent Confidence Score Is Not Statistically Calibrated

**Source:** `agents/quant_agent.py` — LLM JSON output field `confidence`

The `confidence` field (0–1) is produced by the LLM (Qwen3 32B) from its interpretation of the indicator set. It is not derived from a backtested model with historical win-rate calibration.

**Impact:** The conviction thresholds (0.65 for `standard`, 0.80 for `high`) are policy decisions, not probability estimates. A score of 0.80 does not mean the setup has an 80% win rate.

This will be revisited at V2.1, once 30+ closed trades are logged in the SQLite database and a calibration curve can be plotted.
