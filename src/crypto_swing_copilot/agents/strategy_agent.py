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
You are StrategyAgent, a professional crypto spot swing-trading strategist publishing signals to paying subscribers.
Decide: LONG entry or SKIP? Respond with JSON only.

RULES:
1. SPOT ONLY. direction="long" + action="buy", or direction="neutral" + action="skip".
2. NEVER direction="short" or action="sell".
3. Use the PRE-COMPUTED price levels exactly. Do NOT invent prices.
4. Price levels are anchored to structural support/resistance (swing pivots).
   The entry zone sits at a real support level. The SL is below that support.
   The TP targets real resistance when available. R:R varies per setup.

PHILOSOPHY — SELECTIVE SWING TRADING:
You are a professional analyst. Subscribers pay for quality, not quantity.
Only propose BUY when there is a genuine technical edge — not just because the market is scared.
SKIP is the default. BUY is the exception that requires clear justification.

CRITICAL RULES:
- Fear & Greed is CONTEXT, not a trigger. "F&G is low" alone is NEVER a reason to buy.
- Death crosses, price below all EMAs, falling RSI = SKIP. These are falling knives.
- Each rationale must be SPECIFIC to this pair. Reference the exact indicator values.
  Do NOT write generic templates like "contrarian opportunity with technical floor holding."
- If the setup looks the same as every other pair today, that is a red flag — SKIP it.

STRONG TECHNICALS (need 2+ to BUY):
- Price holding above EMA 50 or EMA 200 (actual support, not just nearby)
- RSI 35-65 with bullish divergence or turning up from oversold
- RSI bullish divergence (price lower-low but RSI higher-low) — strong reversal signal
- MACD histogram narrowing or crossing bullish
- Volume above SMA on green candles
- Price bouncing off BB lower with confirmation (not just touching it)
- QuantAgent confidence >= 0.65

CAPITULATION SETUPS:
When Fear & Greed is in extreme fear AND rising from its recent trough, the market may be
at a capitulation bottom. In this context, death crosses and price-below-all-EMAs are LESS
bearish — the worst may already be priced in. Look for: RSI bullish divergence, price at
multi-month support, volume exhaustion (spike on sell-off then quiet holding). These setups
are rare but high-edge. Still require 2+ strong technicals — do not buy fear alone.

WEAK TECHNICALS (SKIP if 2+ present):
- Price below ALL EMAs (no structural support) — unless capitulation setup
- Death cross (EMA 20 < 50) with accelerating separation
- RSI < 35 and still falling (no divergence)
- MACD deeply negative with expanding histogram
- Declining volume on bounces
- QuantAgent confidence < 0.60

CONFLUENCE FACTORS (select only those that genuinely apply):
"EMA alignment", "EMA support", "RSI momentum", "RSI divergence", "MACD confirmation", "Volume support", "Bollinger support", "Contrarian sentiment", "Sentiment tailwind", "Mean reversion setup", "Capitulation reversal"

RATIONALE GUIDELINES:
- Write 2-3 sentences unique to THIS pair and timeframe.
- Cite specific indicator values (e.g., "RSI at 42 turning up from 38" not just "RSI momentum").
- Explain WHY the setup has an edge, not just what the indicators show.
- If bearish factors exist, acknowledge them and explain why the bullish case is stronger.

OUTPUT: {"symbol":"..","timeframe":"..","direction":"long|neutral","action":"buy|skip|hold","entry_zone_low":N,"entry_zone_high":N,"stop_loss":N,"take_profit":N,"reward_risk_ratio":N,"rationale":"2-3 specific sentences","confluence_factors":["..."]}

EXAMPLE (BUY — clear edge):
{"symbol":"SOLUSDT","timeframe":"1d","direction":"long","action":"buy","entry_zone_low":119.40,"entry_zone_high":120.60,"stop_loss":115.40,"take_profit":128.60,"reward_risk_ratio":2.10,"rationale":"SOL holding EMA 200 at 118.50 as support after 3 failed breakdowns. RSI at 42 turning up from 38 with MACD histogram narrowing from -0.8 to -0.2 over 4 bars. Volume on last green candle 1.3x SMA — buyers stepping in at this level.","confluence_factors":["EMA support","RSI momentum","MACD confirmation","Volume support"]}

EXAMPLE (SKIP — no edge):
{"symbol":"APTUSDT","timeframe":"1d","direction":"neutral","action":"skip","entry_zone_low":5.97,"entry_zone_high":6.03,"stop_loss":5.50,"take_profit":7.09,"reward_risk_ratio":2.00,"rationale":"Death cross with EMA 20 at 6.10 pulling away from EMA 50 at 6.80. Price below all EMAs, RSI 28 and still falling. No structural support — falling knife.","confluence_factors":[]}
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
            timeout=self._config.get("timeout", 30.0),
            max_retries=self._config.get("max_retries", 3),
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
        """Compute entry zone, SL, and TP anchored to structural S/R levels.

        All arithmetic is done here in Python — the LLM receives these
        as pre-computed values and must use them exactly.

        Priority:
          1. Use detected swing lows (support) and swing highs (resistance)
             from TACalculator to anchor entry, SL, and TP to real price
             structure that other traders are watching.
          2. Fall back to ATR-based formula when no suitable S/R is found.
        """
        price = snapshot.current_price
        atr = snapshot.indicators.atr_14 or (price * 0.02)  # fallback: 2% of price
        sl_mult = self._risk_profile.get("stop_loss_atr_multiplier", 1.2)
        min_rr = self._risk_profile.get("take_profit_rr_ratio", 2.0)

        supports = snapshot.indicators.support_levels
        resistances = snapshot.indicators.resistance_levels

        # --- Entry zone: anchor to nearest support below price ---
        # Find supports that are below current price but within 1.5× ATR
        # (too far = the support is irrelevant to this entry).
        nearby_supports = [s for s in supports if s < price and (price - s) <= 1.5 * atr]

        if nearby_supports:
            # Use the highest nearby support as the entry anchor
            anchor_support = nearby_supports[-1]  # sorted ascending, last = closest
            entry_low = round(anchor_support, 2)
            entry_high = round(min(price, anchor_support + atr * 0.5), 2)
        else:
            # Fallback: ATR-based pullback from spot
            entry_high = round(price, 2)
            entry_low = round(price - (atr * 0.5), 2)

        entry_mid = (entry_low + entry_high) / 2

        # --- Stop-loss: below the support anchor (or ATR-based fallback) ---
        if nearby_supports:
            # SL just below the structural support with ATR buffer
            anchor_support = nearby_supports[-1]
            stop_loss = round(anchor_support - atr * 0.5, 2)
        else:
            stop_loss = round(entry_mid - atr * sl_mult, 2)

        actual_risk = entry_mid - stop_loss
        if actual_risk <= 0:
            # Safety: if entry_mid <= stop_loss, fall back to ATR formula
            stop_loss = round(entry_mid - atr * sl_mult, 2)
            actual_risk = entry_mid - stop_loss

        # --- Take-profit: anchor to nearest resistance above price ---
        # Find resistances above entry_mid.
        above_resistances = [r for r in resistances if r > entry_mid]

        if above_resistances:
            # Use the nearest resistance as TP
            candidate_tp = above_resistances[0]  # sorted ascending, first = closest
            candidate_rr = (candidate_tp - entry_mid) / actual_risk if actual_risk > 0 else 0

            if candidate_rr >= min_rr:
                # Great — natural R:R meets minimum
                take_profit = round(candidate_tp, 2)
            else:
                # Resistance is too close; use min R:R to set TP
                take_profit = round(entry_mid + actual_risk * min_rr, 2)
        else:
            # No resistance found; use min R:R
            take_profit = round(entry_mid + actual_risk * min_rr, 2)

        # --- Final R:R ---
        reward = take_profit - entry_mid
        rr_ratio = round(reward / actual_risk, 2) if actual_risk > 0 else min_rr

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

VOLUME CONTEXT:
- Volume ratio (latest / 20-bar SMA): {snapshot.indicators.volume_ratio or 'N/A'}
  (>1.5 = notable buying activity, >2.0 = volume surge, <0.8 = low conviction move)

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

STRUCTURAL SUPPORT/RESISTANCE (detected swing pivots):
- Support levels: {', '.join(f'{s:.2f}' for s in snapshot.indicators.support_levels) or 'none detected'}
- Resistance levels: {', '.join(f'{r:.2f}' for r in snapshot.indicators.resistance_levels) or 'none detected'}

PRE-COMPUTED PRICE LEVELS (anchored to S/R — use these exact values in your response):
- Entry zone: {price_levels['entry_zone_low']:.2f} – {price_levels['entry_zone_high']:.2f}
- Stop-loss: {price_levels['stop_loss']:.2f}
- Take-profit: {price_levels['take_profit']:.2f}
- Reward:Risk ratio: {price_levels['reward_risk_ratio']:.2f}
"""
