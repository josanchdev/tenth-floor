"""
SentimentAgent — produces risk narrative and bias label from sentiment data.

Receives a ``SentimentSnapshot`` (Fear & Greed + RSS headlines) and produces
a ``SentimentSignal`` with:
  • Macro sentiment bias (extreme_fear → extreme_greed)
  • Risk narrative (LLM-generated)
  • Key headlines selected by the agent

Usage::

    from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent

    agent = SentimentAgent()
    signal = agent.run(sentiment_snapshot)
"""

from __future__ import annotations

import logging

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_gemini,
    load_agent_config,
    parse_json_response,
)
from crypto_swing_copilot.data.models import SentimentSignal, SentimentSnapshot

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are SentimentAgent, a crypto market sentiment analyst for spot trading.

RULES:
- You receive the Fear & Greed index and recent news headlines.
- Classify the macro sentiment bias and write a concise risk narrative.
- Select the 2-3 most impactful headlines.
- This is for SPOT / LONG-ONLY trading — focus on buy-side sentiment risk.
- Respond ONLY with valid JSON matching the schema below.

OUTPUT JSON SCHEMA:
{
  "bias": "extreme_fear|fear|neutral|greed|extreme_greed",
  "risk_narrative": "2-3 sentence narrative about current market sentiment and risk",
  "key_headlines": ["most impactful headline titles"],
  "fear_greed_value": <quoted from input, integer 0-100>
}

BIAS MAPPING:
- 0-20: extreme_fear
- 21-40: fear
- 41-60: neutral
- 61-80: greed
- 81-100: extreme_greed
"""


class SentimentAgent:
    """Produce risk narrative and bias label from sentiment data."""

    def __init__(self) -> None:
        self._config = load_agent_config("sentiment_agent")
        logger.info("SentimentAgent initialised  model=%s", self._config.get("model"))

    @observe(name="sentiment_agent")
    def run(self, snapshot: SentimentSnapshot) -> SentimentSignal:
        """Analyse sentiment and return a SentimentSignal.

        Parameters
        ----------
        snapshot:
            SentimentSnapshot with F&G index and RSS headlines.

        Returns
        -------
        SentimentSignal
            Bias label, risk narrative, and key headlines.
        """
        user_prompt = self._build_prompt(snapshot)

        raw = call_gemini(
            agent_name="sentiment_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "gemini-2.5-flash"),
            temperature=self._config.get("temperature", 0.3),
            max_output_tokens=self._config.get("max_output_tokens", 512),
        )

        signal = parse_json_response(raw, SentimentSignal)
        logger.info(
            "SentimentSignal  bias=%s  fg=%d  key_headlines=%d",
            signal.bias.value, signal.fear_greed_value,
            len(signal.key_headlines),
        )
        return signal

    @staticmethod
    def _build_prompt(snapshot: SentimentSnapshot) -> str:
        """Build the user prompt from a SentimentSnapshot."""
        headlines_str = ""
        for i, hl in enumerate(snapshot.headlines, 1):
            pub = hl.published.strftime("%Y-%m-%d %H:%M") if hl.published else "unknown"
            headlines_str += f"{i}. [{hl.source}] {hl.title} ({pub})\n"

        trend_str = " → ".join(str(v) for v in snapshot.fear_greed_trend) or "no trend data"

        return f"""\
Analyse the current crypto market sentiment.

FEAR & GREED INDEX:
- Current value: {snapshot.fear_greed_value}
- Current label: {snapshot.fear_greed_label}
- 7-day trend (newest first): {trend_str}

RECENT HEADLINES:
{headlines_str if headlines_str else "No headlines available."}

Respond with JSON only. Remember to quote the fear_greed_value from the input.
"""
