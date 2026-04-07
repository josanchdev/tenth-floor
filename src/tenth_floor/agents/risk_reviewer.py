"""
RiskReviewer — portfolio-level review of all trade proposals.

Sees ALL BUY proposals from TradeAnalyst at once. Makes portfolio-level
decisions: approve/reject, conviction tiers, correlation checks, sector
concentration. Replaces RiskAgent + mechanical gates.

Usage::

    from tenth_floor.agents.risk_reviewer import RiskReviewer

    reviewer = RiskReviewer()
    reviewed = reviewer.run(proposals, macro_signal, open_signals)
"""

from __future__ import annotations

import json
import logging

from langfuse import observe

from tenth_floor.agents.base import (
    call_llm,
    clean_json_response,
    load_agent_config,
    load_risk_profile,
)
from tenth_floor.data.models import MacroSignal, ReviewedSignal, TradeProposal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are RiskReviewer, the chief risk officer for a premium multi-asset signal \
provider. You review ALL of today's trade proposals as a portfolio, not \
individually.

You receive:
1. All BUY proposals from TradeAnalyst (with full reasoning and price levels)
2. The macro environment assessment
3. Currently open signals in the portfolio

YOUR ROLE:
- Review proposals as a PORTFOLIO — not one at a time
- Approve the strongest setups, reject the weakest
- Assign conviction tiers based on setup quality + macro alignment
- Flag correlation risks (two energy stocks, three crypto positions, etc.)
- Consider total portfolio exposure in the current macro environment

CONVICTION TIERS:
- "high": Exceptional setup — strong confluence, macro tailwind, clean structure. \
Confidence >= 0.80 from TradeAnalyst AND your own assessment agrees. \
Suggested risk: 2% of portfolio.
- "standard": Good setup — solid confluence but may have minor headwinds. \
Confidence >= 0.60 from TradeAnalyst. Suggested risk: 1% of portfolio.

WHEN TO REJECT:
- Weak setup that made it through TradeAnalyst (confidence < 0.60)
- Correlated with another approved proposal (e.g. XOM + XLE both energy longs)
- Correlated with an existing open signal
- Macro environment strongly against this asset class
- Too many proposals from same sector — pick the strongest, reject the rest
- Portfolio already has significant exposure to this direction/class

PORTFOLIO RULES:
- Max 1 signal per sector (if two energy longs, pick the better one)
- Max 2 signals per asset class
- Consider existing open signals — don't pile into an already-exposed sector
- In risk-off regimes, be MORE selective (reject borderline setups)
- In risk-on regimes, you can be slightly more permissive

OUTPUT FORMAT (JSON array — one entry per proposal):
[
  {
    "symbol": "<symbol>",
    "verdict": "approve|reject",
    "conviction": "high|standard",
    "reasoning": "<2-3 sentences: why approved/rejected in portfolio context>",
    "risk_notes": "<specific risks for this signal>"
  }
]

IMPORTANT:
- You MUST return an entry for EVERY proposal you receive
- Your reasoning should reference the portfolio context, not just the individual trade
- "This is the strongest setup today because..." is better than "This has good technicals"
"""


class RiskReviewer:
    """Portfolio-level review of all trade proposals."""

    def __init__(self) -> None:
        self._config = load_agent_config("risk_reviewer")
        self._risk_profile = load_risk_profile()
        logger.info("RiskReviewer initialised  model=%s", self._config.get("model"))

    @observe(name="risk_reviewer")
    def run(
        self,
        proposals: list[TradeProposal],
        macro: MacroSignal,
        open_signals: list[dict] | None = None,
    ) -> list[ReviewedSignal]:
        """Review all proposals and return verdicts.

        Parameters
        ----------
        proposals:
            All BUY proposals from TradeAnalyst (already validated by Python).
        macro:
            MacroSignal from MacroAnalyst.
        open_signals:
            Currently open signals from DB (for portfolio state).

        Returns
        -------
        list[ReviewedSignal]
            One verdict per proposal.
        """
        if not proposals:
            return []

        user_prompt = self._build_prompt(proposals, macro, open_signals)

        raw = call_llm(
            agent_name="risk_reviewer",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._config.get("model", "qwen3-32b"),
            temperature=self._config.get("temperature", 0.10),
            max_output_tokens=self._config.get("max_output_tokens", 2048),
            provider=self._config.get("provider", "openai"),
            base_url=self._config.get("base_url", "http://localhost:8000/v1"),
            timeout=self._config.get("timeout", 60.0),
            max_retries=self._config.get("max_retries", 3),
        )

        reviewed = self._parse_response(raw, proposals)

        approved_count = sum(1 for r in reviewed if r.verdict.value == "approve")
        logger.info(
            "RiskReviewer done  total=%d  approved=%d  rejected=%d",
            len(reviewed), approved_count, len(reviewed) - approved_count,
        )
        return reviewed

    def _parse_response(
        self,
        raw: str,
        proposals: list[TradeProposal],
    ) -> list[ReviewedSignal]:
        """Parse the LLM response into ReviewedSignal objects."""
        cleaned = clean_json_response(raw)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse RiskReviewer JSON: %s", exc)
            # Fallback: approve all with standard conviction
            return [
                ReviewedSignal(
                    symbol=p.symbol,
                    verdict="approve",
                    conviction="standard",
                    reasoning="RiskReviewer parse error — defaulting to approve",
                    risk_notes="",
                )
                for p in proposals
            ]

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            logger.warning("RiskReviewer returned unexpected type: %s", type(data).__name__)
            data = []

        # Map by symbol
        review_map: dict[str, dict] = {}
        for item in data:
            if isinstance(item, dict) and "symbol" in item:
                review_map[item["symbol"]] = item

        results = []
        for proposal in proposals:
            item = review_map.get(proposal.symbol)
            if item:
                try:
                    results.append(ReviewedSignal.model_validate(item))
                except Exception as exc:
                    logger.warning(
                        "Failed to validate ReviewedSignal for %s: %s",
                        proposal.symbol, exc,
                    )
                    results.append(ReviewedSignal(
                        symbol=proposal.symbol,
                        verdict="approve",
                        conviction="standard",
                        reasoning="Validation error — defaulting to approve",
                        risk_notes="",
                    ))
            else:
                # LLM didn't return a verdict for this symbol — default approve
                logger.warning("RiskReviewer missing verdict for %s", proposal.symbol)
                results.append(ReviewedSignal(
                    symbol=proposal.symbol,
                    verdict="approve",
                    conviction="standard",
                    reasoning="No RiskReviewer verdict — defaulting to approve",
                    risk_notes="",
                ))

        return results

    @staticmethod
    def _build_prompt(
        proposals: list[TradeProposal],
        macro: MacroSignal,
        open_signals: list[dict] | None,
    ) -> str:
        """Build the user prompt with all proposals and context."""
        # Macro context
        macro_section = (
            f"MACRO REGIME: {macro.regime.value}\n"
            f"{macro.regime_reasoning}\n"
            f"VIX: {macro.vix_level or 'N/A'}  |  "
            f"F&G: {macro.fear_greed_value}  |  "
            f"DXY: {macro.dxy_trend}\n"
        )
        for impact in macro.asset_class_impacts:
            macro_section += (
                f"  - {impact.asset_class}: {impact.outlook} — {impact.reasoning}\n"
            )

        # Proposals — compact format to fit token budget.
        # RiskReviewer needs: symbol, levels, R:R, confidence, and a short
        # rationale summary for portfolio-level correlation/concentration checks.
        proposals_section = ""
        for i, p in enumerate(proposals, 1):
            entry_mid = (p.entry_zone_low + p.entry_zone_high) / 2
            risk = entry_mid - p.stop_loss
            reward = p.take_profit - entry_mid
            rr = round(reward / risk, 2) if risk > 0 else 0
            # Truncate rationale to first sentence for token efficiency
            short_rationale = p.rationale.split(". ")[0] + "."
            proposals_section += (
                f"\n{i}. {p.symbol}  conf={p.confidence:.2f}  "
                f"entry={p.entry_zone_low}–{p.entry_zone_high}  "
                f"SL={p.stop_loss}  TP={p.take_profit}  R:R={rr}\n"
                f"   {short_rationale}\n"
            )

        # Open signals (portfolio state)
        open_section = "CURRENTLY OPEN SIGNALS: None\n"
        if open_signals:
            open_section = f"CURRENTLY OPEN SIGNALS ({len(open_signals)}):\n"
            for sig in open_signals:
                open_section += (
                    f"  - {sig.get('pair', '?')} {sig.get('timeframe', '?')} "
                    f"({sig.get('status', '?')}) — "
                    f"conviction: {sig.get('conviction', '?')}\n"
                )

        return f"""\
Review all of today's trade proposals as a portfolio.

{macro_section}

{open_section}

TODAY'S PROPOSALS ({len(proposals)}):
{proposals_section}

For each proposal, decide: APPROVE or REJECT? What conviction tier?
Consider correlations, sector concentration, and macro alignment.
Return a JSON array with one entry per proposal.
"""
