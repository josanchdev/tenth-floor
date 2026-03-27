"""
SentimentAgent — produces risk narrative and bias label from sentiment data.

Receives a ``SentimentSnapshot`` (Fear & Greed + RSS headlines) and produces
a ``SentimentSignal`` with:
  - Macro sentiment bias (extreme_fear → extreme_greed)
  - Risk narrative (LLM-generated)
  - Key headlines selected by the agent

Usage::

    from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent

    agent = SentimentAgent()
    signal = agent.run(sentiment_snapshot)
"""

from __future__ import annotations

import logging

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_llm,
    load_agent_config,
    parse_json_response,
)
from crypto_swing_copilot.data.models import SentimentSignal, SentimentSnapshot

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are SentimentAgent, a crypto market sentiment analyst for a spot-only signal provider.

You receive the Fear & Greed index and recent news headlines.
Your job is to classify macro sentiment and write a risk narrative for LONG-only traders.

RULES:
- Classify bias using the Fear & Greed value and headline tone together.
- The narrative should help a spot trader decide whether NOW is a good time to enter.
- Select the 2-3 most market-moving headlines. If no headlines are provided, return an empty list.
- You must respond with a JSON object.

BIAS CLASSIFICATION:
- "extreme_fear" (F&G 0-20): Panic selling, potential capitulation — contrarian buy signal
- "fear" (F&G 21-40): Cautious market, risk-off — selective entries only
- "neutral" (F&G 41-60): No strong bias — rely on technicals
- "greed" (F&G 61-80): Bullish momentum — trend-following setups favoured
- "extreme_greed" (F&G 81-100): Euphoria, overextension risk — tighten stops

Headlines can shift the bias ±1 level from what the F&G value alone suggests.
For example, F&G 62 (greed) with a major hack headline could shift to "neutral".

OUTPUT FORMAT:
{
  "bias": "<one of the five bias labels above>",
  "risk_narrative": "<2-3 sentences: what does this mean for a spot LONG trader today?>",
  "key_headlines": ["<most impactful headline titles, max 3>"],
  "fear_greed_value": <integer, quoted from input>
}

EXAMPLE:
{
  "bias": "greed",
  "risk_narrative": "Fear & Greed at 68 with a rising 7-day trend signals sustained buying interest. BTC ETF inflow headlines reinforce institutional demand. Spot long entries on pullbacks are favoured; avoid chasing breakouts at these sentiment levels.",
  "key_headlines": ["BlackRock BTC ETF sees $500M single-day inflow", "Fed signals rate pause through Q3"],
  "fear_greed_value": 68
}
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

        raw = call_llm(
            agent_name="sentiment_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.3),
            max_output_tokens=self._config.get("max_output_tokens", 512),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
            timeout=self._config.get("timeout", 30.0),
            max_retries=self._config.get("max_retries", 3),
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
Analyse the current crypto market sentiment for spot LONG traders.

FEAR & GREED INDEX:
- Current value: {snapshot.fear_greed_value}
- Current label: {snapshot.fear_greed_label}
- 7-day trend (newest first): {trend_str}

RECENT HEADLINES:
{headlines_str if headlines_str else "No headlines available."}
"""
