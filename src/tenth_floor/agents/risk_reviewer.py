"""
RiskReviewer — per-proposal portfolio review.

For each validated TradeProposal, makes ONE LLM call with full context:
the trade thesis, the macro frame, currently open signals, and any
proposals already approved earlier in this same review pass. The CRO
sees the portfolio as it is being built, one decision at a time.

Usage::

    from tenth_floor.agents.risk_reviewer import RiskReviewer

    reviewer = RiskReviewer()
    reviewed = reviewer.run(proposals, macro_signal, open_signals)
"""

from __future__ import annotations

import logging

from langfuse import observe

from tenth_floor.agents.base import (
    call_llm,
    load_agent_config,
    load_risk_profile,
    parse_json_response,
)
from tenth_floor.data.models import (
    MacroSignal,
    ReviewedSignal,
    ReviewVerdict,
    TradeProposal,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are RiskReviewer, the chief risk officer for a premium multi-asset \
signal provider. Your job is to defend the portfolio and the firm's \
reputation. You decide whether each individual trade proposal deserves to \
be published to paying subscribers today.

You receive ONE proposal at a time, but with full context:
1. The trade thesis from TradeAnalyst (entry, SL, TP, full reasoning)
2. The macro environment from MacroAnalyst
3. Currently open signals in the portfolio
4. Proposals already APPROVED earlier in today's review pass

YOUR STANDARD:
A signal is approved only if you would personally take this trade with \
the firm's capital, given the entire portfolio context and macro frame. \
"Good enough" is not good enough — silence is the default. A single weak \
signal published damages subscriber trust more than ten silent days. When \
in doubt, REJECT.

CONVICTION TIERS (only on approve):
- "high": Exceptional setup. Strong confluence, clear macro tailwind, \
clean structure, asymmetric R:R. You would size up on this trade.
- "standard": Solid setup with minor headwinds or moderate confluence. \
Worth taking at standard size.

WHEN TO REJECT:
- Setup is mediocre on its own (TradeAnalyst confidence < 0.65, weak \
confluence, R:R barely above floor)
- Macro environment is hostile to this asset class
- Already-approved proposal today covers the same sector or theme — \
pick the strongest, reject the rest
- Existing open signal in the portfolio already provides this exposure
- Correlated with another open or approved position (e.g. two tech longs, \
two energy plays)
- Asset class concentration: don't pile more than 2 signals into one class

YOUR REPUTATION IS ON THE LINE. Subscribers trade real money on your \
calls. Be the CRO who said no when it mattered.

OUTPUT FORMAT (strict JSON, single object — NOT an array):
{
  "symbol": "<symbol>",
  "verdict": "approve|reject",
  "conviction": "high|standard",
  "reasoning": "<2-3 sentences: why approved/rejected in portfolio + macro context>",
  "risk_notes": "<specific risks for this signal, or empty string>"
}

Even on a reject, you MUST include a conviction field — set it to \
"standard" by convention. Only the verdict and reasoning matter on a reject.
"""


class RiskReviewer:
    """Per-proposal portfolio review with running portfolio state."""

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
        """Review each proposal individually with running portfolio state.

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
            One verdict per proposal, in the same order as the input list.
        """
        if not proposals:
            return []

        # Process strongest setups first so the CRO sees the best ideas
        # before the marginal ones — and so already-approved context is
        # built from the best, not random order.
        ordered = sorted(proposals, key=lambda p: p.confidence, reverse=True)

        approved_so_far: list[tuple[TradeProposal, ReviewedSignal]] = []
        results_by_symbol: dict[str, ReviewedSignal] = {}

        for i, proposal in enumerate(ordered, 1):
            logger.info(
                "RiskReviewer %d/%d  reviewing %s  conf=%.2f",
                i, len(ordered), proposal.symbol, proposal.confidence,
            )
            review = self._review_one(proposal, macro, open_signals or [], approved_so_far)
            results_by_symbol[proposal.symbol] = review
            if review.verdict == ReviewVerdict.APPROVE:
                approved_so_far.append((proposal, review))
                logger.info(
                    "RiskReviewer APPROVE  %s  conviction=%s",
                    proposal.symbol, review.conviction.value,
                )
            else:
                logger.info(
                    "RiskReviewer REJECT  %s  reason=%s",
                    proposal.symbol, review.reasoning[:100],
                )

        # Return in the original input order so callers can map by index
        results = [results_by_symbol[p.symbol] for p in proposals]

        approved_count = sum(1 for r in results if r.verdict == ReviewVerdict.APPROVE)
        logger.info(
            "RiskReviewer done  total=%d  approved=%d  rejected=%d",
            len(results), approved_count, len(results) - approved_count,
        )
        return results

    def _review_one(
        self,
        proposal: TradeProposal,
        macro: MacroSignal,
        open_signals: list[dict],
        approved_so_far: list[tuple[TradeProposal, ReviewedSignal]],
    ) -> ReviewedSignal:
        """Make one LLM call to review a single proposal."""
        user_prompt = self._build_prompt(proposal, macro, open_signals, approved_so_far)

        try:
            raw = call_llm(
                agent_name="risk_reviewer",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=self._config.get("model", "qwen3-32b"),
                temperature=self._config.get("temperature", 0.10),
                max_output_tokens=self._config.get("max_output_tokens", 1024),
                provider=self._config.get("provider", "openai"),
                base_url=self._config.get("base_url", "http://localhost:8000/v1"),
                timeout=self._config.get("timeout", 60.0),
                max_retries=self._config.get("max_retries", 3),
            )
            review = parse_json_response(raw, ReviewedSignal)
        except Exception as exc:
            logger.warning(
                "RiskReviewer failed for %s: %s — defaulting to REJECT",
                proposal.symbol, exc,
            )
            return ReviewedSignal(
                symbol=proposal.symbol,
                verdict="reject",
                conviction="standard",
                reasoning="RiskReviewer error — defaulted to reject for safety",
                risk_notes="",
            )

        # Authoritative symbol — never trust LLM symbol echoing
        if review.symbol != proposal.symbol:
            review = review.model_copy(update={"symbol": proposal.symbol})

        return review

    @staticmethod
    def _build_prompt(
        proposal: TradeProposal,
        macro: MacroSignal,
        open_signals: list[dict],
        approved_so_far: list[tuple[TradeProposal, ReviewedSignal]],
    ) -> str:
        """Build the per-proposal user prompt with full portfolio context."""
        # Macro frame
        macro_section = (
            f"MACRO REGIME: {macro.regime.value}\n"
            f"{macro.regime_reasoning}\n"
            f"VIX: {macro.vix_level or 'N/A'}  |  "
            f"F&G: {macro.fear_greed_value}  |  "
            f"DXY: {macro.dxy_trend}\n"
            "Per-asset-class outlook:\n"
        )
        for impact in macro.asset_class_impacts:
            macro_section += (
                f"  - {impact.asset_class}: {impact.outlook} — {impact.reasoning}\n"
            )

        # The proposal under review — full thesis, not compressed
        entry_mid = (proposal.entry_zone_low + proposal.entry_zone_high) / 2
        risk = entry_mid - proposal.stop_loss
        reward = proposal.take_profit - entry_mid
        rr = round(reward / risk, 2) if risk > 0 else 0

        confluence = "\n".join(f"    - {f}" for f in proposal.confluence_factors) or "    (none provided)"
        risks = "\n".join(f"    - {f}" for f in proposal.risk_factors) or "    (none provided)"

        proposal_section = (
            f"PROPOSAL UNDER REVIEW:\n"
            f"  Symbol: {proposal.symbol}  ({proposal.timeframe})\n"
            f"  TradeAnalyst confidence: {proposal.confidence:.2f}\n"
            f"  Entry zone: {proposal.entry_zone_low} – {proposal.entry_zone_high}\n"
            f"  Stop-loss: {proposal.stop_loss}\n"
            f"  Take-profit: {proposal.take_profit}\n"
            f"  Reward:Risk: {rr}\n"
            f"\n  Trade thesis:\n  {proposal.rationale}\n"
            f"\n  Entry reasoning: {proposal.entry_reasoning}\n"
            f"  Stop reasoning: {proposal.stop_reasoning}\n"
            f"  Target reasoning: {proposal.target_reasoning}\n"
            f"\n  Confluence factors:\n{confluence}\n"
            f"\n  Risk factors:\n{risks}\n"
        )

        # Open signals
        if open_signals:
            open_section = f"CURRENTLY OPEN SIGNALS ({len(open_signals)}):\n"
            for sig in open_signals:
                open_section += (
                    f"  - {sig.get('pair', '?')} {sig.get('timeframe', '?')} "
                    f"({sig.get('status', '?')}) — "
                    f"conviction: {sig.get('conviction', '?')}\n"
                )
        else:
            open_section = "CURRENTLY OPEN SIGNALS: None\n"

        # Already approved earlier in THIS review pass
        if approved_so_far:
            approved_section = (
                f"ALREADY APPROVED IN TODAY'S REVIEW ({len(approved_so_far)}):\n"
            )
            for prop, rev in approved_so_far:
                approved_section += (
                    f"  - {prop.symbol}  conviction={rev.conviction.value}  "
                    f"conf={prop.confidence:.2f}\n"
                    f"    {rev.reasoning}\n"
                )
        else:
            approved_section = "ALREADY APPROVED IN TODAY'S REVIEW: None\n"

        return f"""\
Review the following trade proposal in full portfolio context.

{macro_section}

{open_section}

{approved_section}

{proposal_section}

Decide: APPROVE or REJECT? If approve, what conviction tier?
Consider correlations with open and already-approved positions, sector and \
asset-class concentration, and macro alignment. Return a single JSON object.
"""
