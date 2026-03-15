"""
StrategyAgent — proposes LONG-only trade setups with entry, SL, and TP.

Combines QuantSignal + SentimentSignal + PairSnapshot to produce a
``SetupProposal``. Price levels (SL/TP) are **computed by Python** from
ATR and risk_profile.json parameters — the LLM only decides whether to
enter (LONG) or skip.

CRITICAL: SPOT ONLY — never proposes SHORT. If bearish → action=SKIP.

Usage::

    from crypto_swing_copilot.agents.strategy_agent import StrategyAgent

    agent = StrategyAgent()
    proposal = agent.run(snapshot, quant_signal, sentiment_signal)
"""

from __future__ import annotations

import logging

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_gemini,
    load_agent_config,
    load_risk_profile,
    parse_json_response,
)
from crypto_swing_copilot.data.models import (
    PairSnapshot,
    QuantSignal,
    SentimentSignal,
    SetupProposal,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are StrategyAgent, a crypto spot trading strategist.

CRITICAL RULES:
- SPOT TRADING ONLY. You may ONLY propose LONG positions. NEVER propose SHORT.
- If the market looks bearish, set action="skip" and direction="neutral". NEVER set direction="short".
- Entry zone, stop-loss, and take-profit are PRE-COMPUTED by Python and provided to you.
- You MUST use the provided price levels exactly. DO NOT invent your own prices.
- Respond ONLY with valid JSON matching the schema below.

OUTPUT JSON SCHEMA:
{
  "symbol": "<pair>",
  "timeframe": "<tf>",
  "direction": "long|neutral",
  "action": "buy|hold|skip",
  "entry_zone_low": <pre-computed>,
  "entry_zone_high": <pre-computed>,
  "stop_loss": <pre-computed>,
  "take_profit": <pre-computed>,
  "reward_risk_ratio": <pre-computed>,
  "rationale": "2-3 sentence reasoning combining quant + sentiment signals",
  "confluence_factors": ["list of supporting factors"]
}

ACTION RULES:
- "buy": strong LONG setup with quant + sentiment alignment
- "hold": decent setup but timing not ideal, wait for better entry
- "skip": bearish signals, low confidence, or no clear setup
"""


class StrategyAgent:
    """Propose LONG-only trade setups from quant + sentiment signals."""

    def __init__(self) -> None:
        self._config = load_agent_config("strategy_agent")
        self._risk_profile = load_risk_profile()
        logger.info("StrategyAgent initialised  model=%s", self._config.get("model"))

    @observe(name="strategy_agent")
    def run(
        self,
        snapshot: PairSnapshot,
        quant: QuantSignal,
        sentiment: SentimentSignal,
    ) -> SetupProposal:
        """Combine signals and propose a trade setup.

        Parameters
        ----------
        snapshot:
            PairSnapshot with current price and TA indicators.
        quant:
            QuantSignal from QuantAgent.
        sentiment:
            SentimentSignal from SentimentAgent.

        Returns
        -------
        SetupProposal
            LONG entry proposal or SKIP.
        """
        # Pre-compute price levels (Python, not LLM)
        price_levels = self._compute_price_levels(snapshot)
        user_prompt = self._build_prompt(snapshot, quant, sentiment, price_levels)

        raw = call_gemini(
            agent_name="strategy_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "gemini-2.5-flash"),
            temperature=self._config.get("temperature", 0.1),
            max_output_tokens=self._config.get("max_output_tokens", 2048),
        )

        proposal = parse_json_response(raw, SetupProposal)

        # Safety: force-reject any SHORT that slips through
        if proposal.direction.value == "short":
            logger.warning("StrategyAgent proposed SHORT for %s — overriding to SKIP", snapshot.symbol)
            proposal = SetupProposal(
                symbol=proposal.symbol,
                timeframe=proposal.timeframe,
                direction="neutral",
                action="skip",
                entry_zone_low=proposal.entry_zone_low,
                entry_zone_high=proposal.entry_zone_high,
                stop_loss=proposal.stop_loss,
                take_profit=proposal.take_profit,
                reward_risk_ratio=proposal.reward_risk_ratio,
                rationale="Spot only — bearish signal converted to SKIP.",
                confluence_factors=[],
            )

        logger.info(
            "SetupProposal  %s %s  dir=%s  action=%s  entry=%.2f–%.2f",
            proposal.symbol, proposal.timeframe,
            proposal.direction.value, proposal.action.value,
            proposal.entry_zone_low, proposal.entry_zone_high,
        )
        return proposal

    def _compute_price_levels(self, snapshot: PairSnapshot) -> dict:
        """Compute entry zone, SL, and TP from TA indicators + risk profile.

        All arithmetic is done here in Python — the LLM receives these
        as pre-computed values and must use them exactly.
        """
        price = snapshot.current_price
        atr = snapshot.indicators.atr_14 or (price * 0.02)  # fallback: 2% of price
        sl_mult = self._risk_profile.get("stop_loss_atr_multiplier", 1.2)
        tp_rr = self._risk_profile.get("take_profit_rr_ratio", 2.0)

        # Entry zone: ±0.5% around current price
        entry_low = round(price * 0.995, 2)
        entry_high = round(price * 1.005, 2)

        # Stop-loss: below entry by ATR × multiplier (LONG only)
        sl_distance = atr * sl_mult
        stop_loss = round(entry_low - sl_distance, 2)

        # Take-profit: above entry by SL distance × R:R ratio
        tp_distance = sl_distance * tp_rr
        take_profit = round(entry_high + tp_distance, 2)

        # Reward:Risk ratio
        risk = entry_high - stop_loss
        reward = take_profit - entry_low
        rr_ratio = round(reward / risk, 2) if risk > 0 else tp_rr

        return {
            "entry_zone_low": entry_low,
            "entry_zone_high": entry_high,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reward_risk_ratio": rr_ratio,
        }

    @staticmethod
    def _build_prompt(
        snapshot: PairSnapshot,
        quant: QuantSignal,
        sentiment: SentimentSignal,
        price_levels: dict,
    ) -> str:
        """Build the user prompt with all context."""
        return f"""\
Decide whether to propose a LONG entry or SKIP for this pair.

PAIR: {snapshot.symbol}
TIMEFRAME: {snapshot.timeframe}
CURRENT PRICE: {snapshot.current_price:.2f}

QUANT ANALYSIS (from QuantAgent):
- Trend regime: {quant.trend_regime.value}
- Confidence: {quant.confidence:.2f}
- Signals: {', '.join(quant.signals) or 'none'}
- Reasoning: {quant.reasoning}

SENTIMENT ANALYSIS (from SentimentAgent):
- Bias: {sentiment.bias.value}
- Fear & Greed: {sentiment.fear_greed_value}
- Narrative: {sentiment.risk_narrative}
- Key headlines: {', '.join(sentiment.key_headlines) or 'none'}

PRE-COMPUTED PRICE LEVELS (use these exactly):
- Entry zone: {price_levels['entry_zone_low']:.2f} – {price_levels['entry_zone_high']:.2f}
- Stop-loss: {price_levels['stop_loss']:.2f}
- Take-profit: {price_levels['take_profit']:.2f}
- Reward:Risk ratio: {price_levels['reward_risk_ratio']:.2f}

Remember: LONG or SKIP only. Never SHORT. Use the pre-computed prices exactly.
Respond with JSON only.
"""
