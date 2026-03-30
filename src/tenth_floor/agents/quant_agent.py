"""
QuantAgent — classifies trend regime and technical signals.

Receives a ``PairSnapshot`` and produces a ``QuantSignal`` with:
  - Trend regime (strong_uptrend → strong_downtrend)
  - Signal labels (e.g. "RSI oversold", "EMA golden cross")
  - Confidence score (0–1) based on indicator consensus
  - LLM-generated reasoning

Usage::

    from tenth_floor.agents.quant_agent import QuantAgent

    agent = QuantAgent()
    signal = agent.run(pair_snapshot)
"""

from __future__ import annotations

import logging

from langfuse import observe

from tenth_floor.agents.base import (
    call_llm,
    load_agent_config,
    parse_json_response,
)
from tenth_floor.data.models import PairSnapshot, QuantSignal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are QuantAgent, a technical analysis classifier for crypto spot markets.

You receive pre-computed technical indicators for a single trading pair.
Your job is to classify the trend, identify active signals, and score confidence.

RULES:
- All indicator values are pre-computed by Python. Do NOT recompute any numbers.
- Quote indicator values from the input when referencing them in reasoning.
- Analysis is LONG-biased (spot trading, no shorting).
- You must respond with a JSON object.

TREND REGIMES (pick exactly one):
- "strong_uptrend": Price above all EMAs, bullish MACD, RSI > 60
- "uptrend": Price above EMA 50/200, generally bullish momentum
- "sideways": No clear trend, RSI near 50, mixed EMA signals
- "downtrend": Price below EMA 50/200, generally bearish momentum
- "strong_downtrend": Price below all EMAs, bearish MACD, RSI < 40

CONFIDENCE SCORING (0.0 to 1.0):
- 0.80–1.00: Strong consensus — multiple indicators confirm the same direction
- 0.65–0.79: Moderate consensus — most indicators align, some neutral or mixed
- 0.50–0.64: Weak consensus — conflicting signals, no clear setup
- Below 0.50: Indicators actively contradict each other

SIGNAL LABELS (include all that apply from this list):
- "EMA golden cross (20 > 50)"
- "EMA death cross (20 < 50)"
- "Price above 200 EMA"
- "Price below 200 EMA"
- "RSI oversold (< 30)"
- "RSI overbought (> 70)"
- "RSI bullish divergence"
- "MACD bullish crossover"
- "MACD bearish crossover"
- "Bollinger Band squeeze"
- "Price at BB lower (potential support)"
- "Price at BB upper (potential resistance)"
- "OBV divergence"
- "Volume surge above SMA"

OUTPUT FORMAT:
{
  "symbol": "<pair>",
  "timeframe": "<tf>",
  "trend_regime": "<one of the five regimes above>",
  "signals": ["<signal labels from the list above>"],
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences explaining the classification>"
}

NOTE ON CONFIDENCE:
Your confidence score is informational context for the LLM reasoning summary.
The pipeline's actual gating/conviction decisions use a deterministic trend_score
computed by Python (7-signal indicator agreement, 0–1). Your confidence value
does NOT control whether the signal is published.

EXAMPLE:
{
  "symbol": "ETHUSDT",
  "timeframe": "1d",
  "trend_regime": "uptrend",
  "signals": ["Price above 200 EMA", "MACD bullish crossover", "Volume surge above SMA"],
  "confidence": 0.74,
  "reasoning": "ETH is trading above the 200 EMA at 3420.00 with a fresh MACD bullish crossover. Volume is 1.3x the 20-period SMA, confirming buying pressure. RSI at 58 shows room to run but is not yet overbought."
}
"""


class QuantAgent:
    """Classify trend regime and technical signals from a PairSnapshot."""

    def __init__(self) -> None:
        self._config = load_agent_config("quant_agent")
        logger.info("QuantAgent initialised  model=%s", self._config.get("model"))

    @observe(name="quant_agent")
    def run(self, snapshot: PairSnapshot) -> QuantSignal:
        """Analyse a PairSnapshot and return a QuantSignal.

        Parameters
        ----------
        snapshot:
            Pre-computed PairSnapshot with TA indicators.

        Returns
        -------
        QuantSignal
            Trend classification, signals, confidence, and reasoning.
        """
        user_prompt = self._build_prompt(snapshot)

        raw = call_llm(
            agent_name="quant_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.1),
            max_output_tokens=self._config.get("max_output_tokens", 1024),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
            timeout=self._config.get("timeout", 30.0),
            max_retries=self._config.get("max_retries", 3),
        )

        signal = parse_json_response(raw, QuantSignal)

        # Override LLM symbol with authoritative snapshot symbol
        if signal.symbol != snapshot.symbol:
            signal = signal.model_copy(update={"symbol": snapshot.symbol})

        logger.info(
            "QuantSignal  %s %s  regime=%s  confidence=%.2f  signals=%d",
            signal.symbol, signal.timeframe,
            signal.trend_regime.value, signal.confidence,
            len(signal.signals),
        )
        return signal

    @staticmethod
    def _build_prompt(snapshot: PairSnapshot) -> str:
        """Build the user prompt from a PairSnapshot."""
        ind = snapshot.indicators
        closes_str = ", ".join(f"{c:.2f}" for c in snapshot.recent_closes[:10])

        return f"""\
Analyse this crypto pair and classify its trend regime.

PAIR: {snapshot.symbol}
TIMEFRAME: {snapshot.timeframe}
CURRENT PRICE: {snapshot.current_price:.2f}

TECHNICAL INDICATORS:
- EMA 20: {ind.ema_20}
- EMA 50: {ind.ema_50}
- EMA 200: {ind.ema_200}
- RSI 14: {ind.rsi_14}
- MACD Line: {ind.macd_line}
- MACD Signal: {ind.macd_signal}
- MACD Histogram: {ind.macd_histogram}
- BB Upper: {ind.bb_upper}
- BB Middle: {ind.bb_middle}
- BB Lower: {ind.bb_lower}
- ATR 14: {ind.atr_14}
- OBV: {ind.obv}
- Volume SMA 20: {ind.volume_sma_20}
- Volume Ratio (latest / SMA 20): {ind.volume_ratio}
- RSI Bullish Divergence: {ind.rsi_divergence}

RECENT CLOSES (newest first): [{closes_str}]
"""
