"""
TradeAnalyst — AI-first trade analysis with LLM-chosen price levels.

Replaces QuantAgent + StrategyAgent. One coherent analysis per asset with
full context: macro frame, TA indicators, structural levels, volume.

The LLM decides BUY or SKIP. If BUY, it picks entry zone, SL, TP with
structural reasoning. Python validates the output for sanity but does not
override the LLM's judgment.

Usage::

    from tenth_floor.agents.trade_analyst import TradeAnalyst

    agent = TradeAnalyst()
    proposal = agent.run(snapshot, macro_signal)
"""

from __future__ import annotations

import logging

from langfuse import observe

from tenth_floor.agents.base import (
    call_llm,
    load_agent_config,
    parse_json_response,
)
from tenth_floor.data.models import (
    MacroSignal,
    PairSnapshot,
    SetupAction,
    SignalDirection,
    TradeProposal,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are TradeAnalyst, a senior portfolio manager at a multi-asset hedge fund \
with 20+ years of experience across crypto, US equities, ETFs, and commodities.

You receive technical indicators, structural levels, and a macro regime \
assessment. Your job: find the best swing trade setup for this asset, or pass \
if there genuinely isn't one.

Think like a fund manager. Every regime has opportunities — risk-off favours \
safe havens and oversold bounces, risk-on favours momentum. Your job is to \
find them. Be decisive and opinionated.

CONSTRAINTS:
- Spot longs only. action="buy" + direction="long", or action="skip" + direction="neutral".
- Entry zone should be near current price (subscribers act immediately).
- A separate system validates your math (SL < entry < TP, R:R >= 1.5). \
Focus on finding good setups — the math check is automatic.

OUTPUT FORMAT (strict JSON):
{
  "symbol": "<symbol>",
  "timeframe": "1d",
  "action": "buy|skip",
  "direction": "long|neutral",
  "entry_zone_low": <float>,
  "entry_zone_high": <float>,
  "stop_loss": <float>,
  "take_profit": <float>,
  "confidence": <float 0.0-1.0>,
  "rationale": "<your full trade thesis or reason for skipping>",
  "entry_reasoning": "<why this entry zone>",
  "stop_reasoning": "<why this SL level>",
  "target_reasoning": "<why this TP level>",
  "confluence_factors": ["<what supports this trade>"],
  "risk_factors": ["<what could go wrong>"]
}
"""


class TradeAnalyst:
    """AI-first trade analysis — decides BUY/SKIP and picks price levels."""

    def __init__(self) -> None:
        self._config = load_agent_config("trade_analyst")
        logger.info("TradeAnalyst initialised  model=%s", self._config.get("model"))

    @observe(name="trade_analyst")
    def run(
        self,
        snapshot: PairSnapshot,
        macro: MacroSignal,
    ) -> TradeProposal:
        """Analyse an asset and return a TradeProposal.

        Parameters
        ----------
        snapshot:
            PairSnapshot with current price, TA indicators, and structure.
        macro:
            MacroSignal from MacroAnalyst — the macro frame for this analysis.

        Returns
        -------
        TradeProposal
            BUY proposal with LLM-chosen levels, or SKIP with reasoning.
        """
        user_prompt = self._build_prompt(snapshot, macro)

        raw = call_llm(
            agent_name="trade_analyst",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.10),
            max_output_tokens=self._config.get("max_output_tokens", 1024),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
            timeout=self._config.get("timeout", 30.0),
            max_retries=self._config.get("max_retries", 3),
        )

        proposal = parse_json_response(raw, TradeProposal)

        # SKIPs often return 0 for price fields — fill with dummy values so
        # the model validates.  These proposals are discarded anyway.
        if proposal.action == SetupAction.SKIP:
            dummy_updates: dict[str, float] = {}
            for field in ("entry_zone_low", "entry_zone_high", "stop_loss", "take_profit"):
                if getattr(proposal, field) <= 0:
                    dummy_updates[field] = 1.0
            if dummy_updates:
                proposal = proposal.model_copy(update=dummy_updates)

        # Override LLM symbol with authoritative snapshot symbol
        if proposal.symbol != snapshot.symbol:
            proposal = proposal.model_copy(update={"symbol": snapshot.symbol})

        # Safety: force-reject any SHORT that slips through
        if proposal.direction.value == "short":
            logger.warning(
                "TradeAnalyst proposed SHORT for %s — overriding to SKIP",
                snapshot.symbol,
            )
            proposal = proposal.model_copy(update={
                "direction": SignalDirection.NEUTRAL,
                "action": SetupAction.SKIP,
                "rationale": "Spot only — bearish signal converted to SKIP.",
                "confluence_factors": [],
            })

        logger.info(
            "TradeProposal  %s %s  action=%s  conf=%.2f  entry=%.4f–%.4f  "
            "SL=%.4f  TP=%.4f",
            proposal.symbol, proposal.timeframe,
            proposal.action.value, proposal.confidence,
            proposal.entry_zone_low, proposal.entry_zone_high,
            proposal.stop_loss, proposal.take_profit,
        )
        return proposal

    @staticmethod
    def _build_prompt(snapshot: PairSnapshot, macro: MacroSignal) -> str:
        """Build the user prompt with full context."""
        ind = snapshot.indicators
        closes_str = ", ".join(f"{c:.2f}" for c in snapshot.recent_closes[:10])
        volumes_str = ", ".join(f"{v:.0f}" for v in snapshot.recent_volumes[:10])

        # Find the asset class impact from macro signal
        asset_class_outlook = "No specific outlook available"
        asset_class_label = snapshot.asset_class or "unknown"
        for impact in macro.asset_class_impacts:
            if impact.asset_class.lower() == asset_class_label.lower():
                asset_class_outlook = f"{impact.outlook}: {impact.reasoning}"
                break

        supports_str = (
            ", ".join(f"{s:.2f}" for s in ind.support_levels)
            or "none detected"
        )
        resistances_str = (
            ", ".join(f"{r:.2f}" for r in ind.resistance_levels)
            or "none detected"
        )

        return f"""\
The macro environment today is: {macro.regime.value}
{macro.regime_reasoning}
Asset class outlook ({asset_class_label}): {asset_class_outlook}
{f"Alerts: {', '.join(macro.alerts)}" if macro.alerts else ""}

Given this macro context, analyse the following asset for a potential swing trade.

SYMBOL: {snapshot.symbol}
ASSET CLASS: {asset_class_label}
TIMEFRAME: {snapshot.timeframe}
CURRENT PRICE: {snapshot.current_price}

TECHNICAL INDICATORS (computed by Python — use for reasoning, do not recompute):
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

STRUCTURAL LEVELS (detected swing pivots):
- Support levels: {supports_str}
- Resistance levels: {resistances_str}

RECENT CLOSES (newest first): [{closes_str}]
RECENT VOLUMES (newest first): [{volumes_str}]

Is this a swing trade worth taking? If yes, provide the complete plan with \
entry, SL, TP, and reasoning for each level.
"""
