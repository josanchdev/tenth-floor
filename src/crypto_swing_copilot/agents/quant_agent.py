"""
QuantAgent — classifies trend regime and technical signals.

Receives a ``PairSnapshot`` and produces a ``QuantSignal`` with:
  • Trend regime (strong_uptrend → strong_downtrend)
  • Signal labels (e.g. "RSI oversold", "EMA golden cross")
  • Confidence score (0–1)
  • LLM-generated reasoning

Usage::

    from crypto_swing_copilot.agents.quant_agent import QuantAgent

    agent = QuantAgent()
    signal = agent.run(pair_snapshot)
"""

from __future__ import annotations

import logging

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_gemini,
    load_agent_config,
    parse_json_response,
)
from crypto_swing_copilot.data.models import PairSnapshot, QuantSignal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are QuantAgent, a technical analysis classifier for crypto spot trading.

RULES:
- You receive pre-computed technical indicators. DO NOT recompute any numbers.
- Classify the trend regime and identify technical signals.
- Assign a confidence score (0.0–1.0) based on indicator consensus.
- Your output is LONG-biased analysis only (spot trading, no shorting).
- Respond ONLY with valid JSON matching the schema below.

OUTPUT JSON SCHEMA:
{
  "symbol": "<pair>",
  "timeframe": "<tf>",
  "trend_regime": "strong_uptrend|uptrend|sideways|downtrend|strong_downtrend",
  "signals": ["list of signal labels"],
  "confidence": 0.0-1.0,
  "reasoning": "explanation"
}

SIGNAL EXAMPLES:
- "EMA golden cross (20 > 50)"
- "RSI oversold (< 30)"
- "MACD bullish crossover"
- "Price above 200 EMA"
- "Bollinger Band squeeze"
- "OBV divergence"
- "Volume surge above SMA"
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

        raw = call_gemini(
            agent_name="quant_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "gemini-2.5-flash"),
            temperature=self._config.get("temperature", 0.1),
            max_output_tokens=self._config.get("max_output_tokens", 1024),
        )

        signal = parse_json_response(raw, QuantSignal)
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

RECENT CLOSES (newest first): [{closes_str}]

Respond with JSON only.
"""
