"""
StrategyAgent — proposes LONG-only trade setups with entry, SL, and TP.

Combines QuantSignal + SentimentSignal + PairSnapshot to produce a
``SetupProposal``. Price levels (SL/TP) are **computed by Python** from
ATR and risk_profile.json parameters — the LLM only decides whether to
enter (LONG) or skip and writes a rationale.

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
    call_llm,
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
You are StrategyAgent, a contrarian crypto spot swing-trading strategist.
Decide: LONG entry or SKIP? Respond with JSON only.

RULES:
1. SPOT ONLY. direction="long" + action="buy", or direction="neutral" + action="skip".
2. NEVER direction="short" or action="sell".
3. Use the PRE-COMPUTED price levels exactly. Do NOT invent prices.

PHILOSOPHY — CONTRARIAN SWING TRADING:
Technicals drive the entry decision. Sentiment adjusts conviction, never gates it.

SENTIMENT MATRIX:
- Extreme fear + strong technicals = BUY (best opportunities — crowd panic + chart support)
- Fear + strong technicals = BUY (crowd scared, chart solid)
- Neutral/Greed + strong technicals = BUY (standard/trend-follow)
- ANY sentiment + weak technicals = SKIP (no technical basis)

STRONG TECHNICALS (need 2+): BB lower support holding, RSI 30-65, MACD positive/converging, volume above SMA, price above 50 or 200 EMA, bullish QuantAgent signals.
WEAK TECHNICALS (skip if 2+): price below ALL EMAs, RSI<30 falling, MACD deeply negative, declining volume, death cross accelerating, confidence<0.50.

BUY when: 2+ strong technical factors, confidence>=0.50, clear thesis.
SKIP when: <2 strong factors, confidence<0.50, falling knife, choppy/no setup.

CONFLUENCE FACTORS: "EMA alignment", "EMA support", "RSI momentum", "MACD confirmation", "Volume support", "Bollinger support", "Contrarian sentiment", "Sentiment tailwind", "Mean reversion setup"

OUTPUT: {"symbol":"..","timeframe":"..","direction":"long|neutral","action":"buy|skip|hold","entry_zone_low":N,"entry_zone_high":N,"stop_loss":N,"take_profit":N,"reward_risk_ratio":N,"rationale":"2-3 sentences","confluence_factors":["..."]}

EXAMPLE (BUY — contrarian):
{"symbol":"SOLUSDT","timeframe":"4h","direction":"long","action":"buy","entry_zone_low":119.40,"entry_zone_high":120.60,"stop_loss":115.40,"take_profit":128.60,"reward_risk_ratio":2.10,"rationale":"SOL at BB lower support with RSI 42 and narrowing MACD. F&G 15 = contrarian opportunity with technical floor holding.","confluence_factors":["Bollinger support","RSI momentum","MACD confirmation","Contrarian sentiment"]}

EXAMPLE (SKIP — falling knife):
{"symbol":"APTUSDT","timeframe":"1d","direction":"neutral","action":"skip","entry_zone_low":5.97,"entry_zone_high":6.03,"stop_loss":5.50,"take_profit":7.09,"reward_risk_ratio":2.00,"rationale":"Death cross, price below all EMAs, RSI 28 falling. No technical floor despite extreme fear — falling knife.","confluence_factors":[]}
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

        raw = call_llm(
            agent_name="strategy_agent",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.1),
            max_output_tokens=self._config.get("max_output_tokens", 2048),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
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

        # Entry zone: ±0.5% around current price  # KNOWN LIMITATION
        entry_low = round(price * 0.995, 2)
        entry_high = round(price * 1.005, 2)
        entry_mid = (entry_low + entry_high) / 2

        # Stop-loss: below entry midpoint by ATR × multiplier (LONG only)
        sl_distance = atr * sl_mult
        stop_loss = round(entry_mid - sl_distance, 2)

        # Take-profit: above entry midpoint by SL distance × R:R ratio
        # Compute from actual risk (after SL rounding) to guarantee R:R >= target
        actual_risk = entry_mid - stop_loss
        take_profit = round(entry_mid + actual_risk * tp_rr, 2)

        # Reward:Risk ratio (symmetric from midpoint)
        reward = take_profit - entry_mid
        rr_ratio = round(reward / actual_risk, 2) if actual_risk > 0 else tp_rr

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

PRE-COMPUTED PRICE LEVELS (use these exact values in your response):
- Entry zone: {price_levels['entry_zone_low']:.2f} – {price_levels['entry_zone_high']:.2f}
- Stop-loss: {price_levels['stop_loss']:.2f}
- Take-profit: {price_levels['take_profit']:.2f}
- Reward:Risk ratio: {price_levels['reward_risk_ratio']:.2f}
"""
