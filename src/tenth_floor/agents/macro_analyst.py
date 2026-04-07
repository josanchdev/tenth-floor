"""
MacroAnalyst — produces macro regime assessment and per-asset-class impact.

Runs ONCE per pipeline. Its output frames every TradeAnalyst call.

V1 (Phase 1.5): VIX + Fear & Greed + DXY.
V2 (Phase 2): Add RSS feeds, 10Y yield, earnings calendar, FRED data.

Usage::

    from tenth_floor.agents.macro_analyst import MacroAnalyst

    agent = MacroAnalyst()
    signal = agent.run(sentiment_snapshot, vix_data, dxy_data)
"""

from __future__ import annotations

import logging

from langfuse import observe

from tenth_floor.agents.base import (
    call_llm,
    load_agent_config,
    parse_json_response,
)
from tenth_floor.data.models import MacroSignal, SentimentSnapshot

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are MacroAnalyst, a senior macro strategist at a multi-asset hedge fund \
covering crypto, US equities, ETFs, and commodities.

You receive macro indicators and produce a regime assessment that frames \
every trade analysis today. Your output is the opening context for the \
entire portfolio team.

Every regime has winners and losers. Identify both. Risk-off rotates capital \
into safe havens — that is opportunity, not absence of it.

Regime options: "risk_on", "risk_off", "mixed", "transitioning".
DXY trend options: "strengthening", "weakening", "stable".
Per-class outlook options: "bullish", "bearish", "neutral".

OUTPUT FORMAT (strict JSON):
{
  "regime": "<risk_on|risk_off|mixed|transitioning>",
  "regime_reasoning": "<your macro assessment>",
  "asset_class_impacts": [
    {"asset_class": "crypto", "outlook": "<bullish|bearish|neutral>", \
"reasoning": "<1-2 sentences>"},
    {"asset_class": "equity", "outlook": "...", "reasoning": "..."},
    {"asset_class": "etf", "outlook": "...", "reasoning": "..."},
    {"asset_class": "commodity", "outlook": "...", "reasoning": "..."}
  ],
  "alerts": ["<anything notable>"],
  "vix_level": <float or null>,
  "fear_greed_value": <int 0-100>,
  "dxy_trend": "<strengthening|weakening|stable>"
}
"""


class MacroAnalyst:
    """Produce macro regime assessment from VIX, F&G, and DXY data."""

    def __init__(self) -> None:
        self._config = load_agent_config("macro_analyst")
        logger.info("MacroAnalyst initialised  model=%s", self._config.get("model"))

    @observe(name="macro_analyst")
    def run(
        self,
        sentiment: SentimentSnapshot,
        vix_data: dict | None = None,
        dxy_data: dict | None = None,
    ) -> MacroSignal:
        """Analyse macro environment and return a MacroSignal.

        Parameters
        ----------
        sentiment:
            SentimentSnapshot with F&G index and RSS headlines.
        vix_data:
            Dict with 'level' (float), 'change_pct' (float), 'trend' (str).
            None if VIX data unavailable.
        dxy_data:
            Dict with 'level' (float), 'change_pct' (float), 'trend' (str).
            None if DXY data unavailable.

        Returns
        -------
        MacroSignal
            Macro regime, per-class impacts, and alerts.
        """
        user_prompt = self._build_prompt(sentiment, vix_data, dxy_data)

        raw = call_llm(
            agent_name="macro_analyst",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.2),
            max_output_tokens=self._config.get("max_output_tokens", 1024),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
            timeout=self._config.get("timeout", 30.0),
            max_retries=self._config.get("max_retries", 3),
        )

        signal = parse_json_response(raw, MacroSignal)
        logger.info(
            "MacroSignal  regime=%s  fg=%d  vix=%s  dxy=%s",
            signal.regime.value,
            signal.fear_greed_value,
            signal.vix_level,
            signal.dxy_trend,
        )
        return signal

    @staticmethod
    def _build_prompt(
        sentiment: SentimentSnapshot,
        vix_data: dict | None,
        dxy_data: dict | None,
    ) -> str:
        """Build the user prompt with all macro context."""
        fg_trend_str = (
            " → ".join(str(v) for v in sentiment.fear_greed_trend)
            or "no trend data"
        )

        headlines_str = ""
        for i, hl in enumerate(sentiment.headlines[:5], 1):
            headlines_str += f"{i}. [{hl.source}] {hl.title}\n"

        vix_section = "VIX DATA: Not available\n"
        if vix_data:
            vix_section = (
                f"VIX DATA:\n"
                f"- Current level: {vix_data.get('level', 'N/A')}\n"
                f"- Change: {vix_data.get('change_pct', 'N/A')}%\n"
                f"- 5-day trend: {vix_data.get('trend', 'N/A')}\n"
            )

        dxy_section = "DXY (US DOLLAR INDEX) DATA: Not available\n"
        if dxy_data:
            dxy_section = (
                f"DXY (US DOLLAR INDEX) DATA:\n"
                f"- Current level: {dxy_data.get('level', 'N/A')}\n"
                f"- Change: {dxy_data.get('change_pct', 'N/A')}%\n"
                f"- 5-day trend: {dxy_data.get('trend', 'N/A')}\n"
            )

        return f"""\
Assess the current macro environment for multi-asset swing trading.

FEAR & GREED INDEX:
- Current value: {sentiment.fear_greed_value}
- Label: {sentiment.fear_greed_label}
- 7-day trend (newest first): {fg_trend_str}

{vix_section}
{dxy_section}
RECENT HEADLINES:
{headlines_str if headlines_str else "No headlines available."}

Based on this data, classify the macro regime and assess impact on each asset class \
(crypto, equity, etf, commodity).
"""
