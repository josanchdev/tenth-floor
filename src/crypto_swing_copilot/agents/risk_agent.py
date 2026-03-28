"""
RiskAgent — final gatekeeper: conviction tier assignment and SHORT rejection.

Receives ``(SetupProposal, confidence_score)`` pairs and produces
``PlaybookEntry[]`` with conviction tiers and suggested risk percentages.

This agent is **mostly Python logic** with a thin LLM layer for verdict reasoning:
  - Reject SHORT proposals → "Spot only – no shorting"
  - Reject skip/hold actions from StrategyAgent
  - Skip low-confidence setups (confidence < 0.65)
  - Assign conviction tier and suggested_risk_pct (Python, not LLM)

Conviction tier logic (from risk_profile.json):
  - confidence >= 0.80 → high    → suggested_risk_pct = 0.02
  - confidence >= 0.65 → standard → suggested_risk_pct = 0.01
  - confidence <  0.65 → SKIP, do not publish

Usage::

    from crypto_swing_copilot.agents.risk_agent import RiskAgent

    agent = RiskAgent()
    entries = agent.run([(proposal, 0.82), (proposal2, 0.71)])
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_llm,
    clean_json_response,
    load_agent_config,
    load_risk_profile,
)
from crypto_swing_copilot.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupProposal,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are RiskAgent, the final risk gatekeeper for a crypto spot signal provider.

Your job is to write a brief verdict_reasoning (1-2 sentences) for each setup.
The actual verdict (approved/rejected) and conviction tier are computed by Python.

You receive the setup details and the Python-computed verdict. Write concise reasoning
that a subscriber would find useful — explain WHY the setup is approved or rejected.

RULES:
- SPOT ONLY. Any SHORT proposal is automatically rejected.
- You must respond with a JSON array of objects with "symbol" and "verdict_reasoning".

OUTPUT FORMAT:
[
  {"symbol": "BTCUSDT", "verdict_reasoning": "<1-2 sentences>"},
  {"symbol": "ETHUSDT", "verdict_reasoning": "<1-2 sentences>"}
]
"""


class RiskAgent:
    """Final gatekeeper — conviction tier assignment, filtering, and playbook assembly."""

    def __init__(self) -> None:
        self._config = load_agent_config("risk_agent")
        self._risk_profile = load_risk_profile()
        self._conviction_tiers = self._risk_profile.get("conviction_tiers", {})
        logger.info("RiskAgent initialised  model=%s", self._config.get("model"))

    @observe(name="risk_agent")
    def run(
        self,
        proposals: list[tuple[SetupProposal, float]],
    ) -> list[PlaybookEntry]:
        """Evaluate proposals and produce final PlaybookEntry list.

        Parameters
        ----------
        proposals:
            List of ``(SetupProposal, confidence_score)`` tuples.
            The confidence score comes from QuantAgent.

        Returns
        -------
        list[PlaybookEntry]
            Final vetted entries for the daily playbook, sorted by rank.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        entries: list[PlaybookEntry] = []

        for rank, (proposal, confidence) in enumerate(proposals, 1):
            # --- Python-computed verdict ---
            verdict, reason = self._compute_verdict(proposal, confidence)

            # --- Conviction tier lookup ---
            conviction, suggested_risk_pct = self._resolve_conviction(confidence)

            entry = PlaybookEntry(
                symbol=proposal.symbol,
                timeframe=proposal.timeframe,
                report_date=today,
                verdict=verdict,
                verdict_reasoning=reason,
                direction=proposal.direction,
                action=proposal.action,
                entry_zone_low=proposal.entry_zone_low,
                entry_zone_high=proposal.entry_zone_high,
                stop_loss=proposal.stop_loss,
                take_profit=proposal.take_profit,
                reward_risk_ratio=proposal.reward_risk_ratio,
                confidence_score=confidence,
                conviction=conviction,
                suggested_risk_pct=suggested_risk_pct,
                strategy_rationale=proposal.rationale,
                rank=rank,
            )
            entries.append(entry)

        # Ask LLM to generate reasoning summaries
        entries = self._enrich_with_llm_reasoning(entries)

        logger.info(
            "RiskAgent produced %d entries  approved=%d  rejected=%d",
            len(entries),
            sum(1 for e in entries if e.verdict == PlaybookVerdict.APPROVED),
            sum(1 for e in entries if e.verdict == PlaybookVerdict.REJECTED),
        )
        return entries

    def _compute_verdict(
        self,
        proposal: SetupProposal,
        confidence: float,
    ) -> tuple[PlaybookVerdict, str]:
        """Pure Python verdict computation — no LLM involved."""
        # Rule 1: Reject SHORT
        if proposal.direction.value == "short":
            return PlaybookVerdict.REJECTED, "Spot only – no shorting"

        # Rule 2: Skip/hold actions
        if proposal.action.value in ("skip", "hold"):
            return PlaybookVerdict.REJECTED, f"Strategy action is {proposal.action.value}"

        # Rule 3: R:R below configured minimum
        min_rr = self._risk_profile.get("take_profit_rr_ratio", 2.0)
        if proposal.reward_risk_ratio < min_rr:
            return (
                PlaybookVerdict.REJECTED,
                f"R:R {proposal.reward_risk_ratio:.2f} below minimum {min_rr:.1f} – skipping",
            )

        # Rule 4: Confidence too low to publish
        min_confidence = self._risk_profile.get("min_setup_confidence", 0.65)
        if confidence < min_confidence:
            return (
                PlaybookVerdict.REJECTED,
                f"Confidence {confidence:.2f} below minimum {min_confidence:.2f} – skipping",
            )

        # Approved
        return PlaybookVerdict.APPROVED, "Signal approved – meets conviction threshold"

    def _resolve_conviction(self, confidence: float) -> tuple[str, float]:
        """Map confidence score to conviction tier and suggested risk percentage.

        Returns
        -------
        tuple[str, float]
            ``(conviction_label, suggested_risk_pct)``.
            Falls back to ``("none", 0.0)`` if confidence is below all tiers.
        """
        high = self._conviction_tiers.get("high", {})
        standard = self._conviction_tiers.get("standard", {})

        if confidence >= high.get("min_confidence", 0.80):
            return "high", high.get("suggested_risk_pct", 0.02)

        if confidence >= standard.get("min_confidence", 0.65):
            return "standard", standard.get("suggested_risk_pct", 0.01)

        return "none", 0.0

    def _enrich_with_llm_reasoning(self, entries: list[PlaybookEntry]) -> list[PlaybookEntry]:
        """Optionally enrich entries with LLM-generated reasoning summaries."""
        if not entries:
            return entries

        # Build context for LLM
        summaries_prompt = "Write brief verdict reasoning (1-2 sentences each) for these setups:\n\n"
        for e in entries:
            summaries_prompt += (
                f"- {e.symbol} {e.timeframe}: verdict={e.verdict.value}, "
                f"direction={e.direction.value}, action={e.action.value}, "
                f"RR={e.reward_risk_ratio:.1f}, conviction={e.conviction}\n"
            )

        try:
            raw = call_llm(
                agent_name="risk_agent",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=summaries_prompt,
                model=self._config.get("model", "qwen3-32b"),
                temperature=self._config.get("temperature", 0.0),
                max_output_tokens=self._config.get("max_output_tokens", 512),
                provider=self._config.get("provider", "openai"),
                base_url=self._config.get("base_url", "http://localhost:8000/v1"),
                timeout=self._config.get("timeout", 30.0),
                max_retries=self._config.get("max_retries", 3),
            )

            # Parse reasoning — handle both list and single-object responses
            cleaned = clean_json_response(raw)
            data = json.loads(cleaned)

            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                logger.warning("LLM returned unexpected type %s — skipping enrichment", type(data).__name__)
                return entries

            reasoning_map: dict[str, str] = {}
            for item in data:
                if isinstance(item, dict) and "symbol" in item and "verdict_reasoning" in item:
                    reasoning_map[item["symbol"]] = item["verdict_reasoning"]

            enriched = []
            for e in entries:
                # Only enrich APPROVED entries — REJECTED keep Python-computed reasons
                if e.verdict == PlaybookVerdict.APPROVED and e.symbol in reasoning_map:
                    enriched.append(e.model_copy(update={"verdict_reasoning": reasoning_map[e.symbol]}))
                else:
                    enriched.append(e)
            return enriched

        except Exception as exc:
            logger.warning("LLM reasoning enrichment failed: %s — using Python-generated reasons", exc)
            return entries  # fallback to Python-generated reasons
