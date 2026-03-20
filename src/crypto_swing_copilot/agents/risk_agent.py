"""
RiskAgent — final gatekeeper: position sizing, SHORT rejection, fee flagging.

Receives ``SetupProposal[]`` + portfolio state and produces ``PlaybookEntry[]``.

This agent is **mostly Python logic** with a thin LLM layer for verdict reasoning:
  • Reject SHORT proposals → "Spot only – no shorting"
  • Flag positions < min_position_eur → "Fees too high"
  • Enforce max_open_positions gate → HOLD if full
  • Compute position size from risk_profile.json (Python, not LLM)

Usage::

    from crypto_swing_copilot.agents.risk_agent import RiskAgent

    agent = RiskAgent()
    entries = agent.run(proposals, portfolio_state)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from langfuse import observe

from crypto_swing_copilot.agents.base import (
    call_gemini,
    load_agent_config,
    load_risk_profile,
)
from crypto_swing_copilot.config import PROJECT_ROOT
from crypto_swing_copilot.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupProposal,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are RiskAgent, the final risk gatekeeper for a crypto spot trading system.

Your job is to write a brief verdict_reasoning (1-2 sentences) for each setup.
The actual verdict (approved/reduced/rejected) and position sizing are computed by Python.

You receive the setup details and the Python-computed verdict. Write concise reasoning.

RULES:
- SPOT ONLY. Reject any SHORT proposal.
- If portfolio is full (max positions reached), mark as HOLD.
- Respond ONLY with valid JSON: a list of objects with "symbol" and "verdict_reasoning".
"""


class RiskAgent:
    """Final gatekeeper — position sizing, filtering, and playbook assembly."""

    def __init__(self) -> None:
        self._config = load_agent_config("risk_agent")
        self._risk_profile = load_risk_profile()
        logger.info("RiskAgent initialised  model=%s", self._config.get("model"))

    @observe(name="risk_agent")
    def run(
        self,
        proposals: list[SetupProposal],
        portfolio_state: dict | None = None,
    ) -> list[PlaybookEntry]:
        """Evaluate proposals and produce final PlaybookEntry list.

        Parameters
        ----------
        proposals:
            List of SetupProposal from StrategyAgent.
        portfolio_state:
            Portfolio state from ``positions.json``. If None, loads from disk.

        Returns
        -------
        list[PlaybookEntry]
            Final vetted entries for the daily playbook, sorted by rank.
        """
        if portfolio_state is None:
            portfolio_state = self._load_portfolio()

        # Compute portfolio context
        cash = portfolio_state.get("cash_eur", 100.0)
        open_positions = portfolio_state.get("open_positions", [])
        n_open = len(open_positions)
        max_positions = self._risk_profile.get("max_open_positions", 3)
        min_position = self._risk_profile.get("min_position_eur", 20)
        fee_pct = self._risk_profile.get("trading_fees_pct", 0.001)
        portfolio_full = n_open >= max_positions

        # Available per trade
        slots_available = max(max_positions - n_open, 0)
        if slots_available > 0:
            available_per_trade = cash / slots_available
        else:
            available_per_trade = 0.0

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries: list[PlaybookEntry] = []

        for rank, proposal in enumerate(proposals, 1):
            # --- Python-computed verdict ---
            verdict, reason = self._compute_verdict(
                proposal=proposal,
                available_per_trade=available_per_trade,
                min_position=min_position,
                portfolio_full=portfolio_full,
            )

            # Position size (fraction of portfolio)
            portfolio_value = portfolio_state.get("portfolio_value_eur", 100.0)
            if verdict == PlaybookVerdict.APPROVED and portfolio_value > 0:
                net_size = available_per_trade * (1 - fee_pct)
                position_size_pct = round(net_size / portfolio_value, 4)
            else:
                position_size_pct = 0.0

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
                position_size_pct=position_size_pct,
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
        available_per_trade: float,
        min_position: float,
        portfolio_full: bool,
    ) -> tuple[PlaybookVerdict, str]:
        """Pure Python verdict computation — no LLM involved."""
        # Rule 1: Reject SHORT
        if proposal.direction.value == "short":
            return PlaybookVerdict.REJECTED, "Spot only – no shorting"

        # Rule 2: Skip/hold actions
        if proposal.action.value in ("skip", "hold"):
            return PlaybookVerdict.REJECTED, f"Strategy action is {proposal.action.value}"

        # Rule 3: Portfolio full
        if portfolio_full:
            return PlaybookVerdict.REJECTED, "Portfolio full – max positions reached. Close a position before entering."

        # Rule 4: Position too small (fees kill it)
        if available_per_trade < min_position:
            return PlaybookVerdict.REJECTED, f"⚠️ Fees too high – position €{available_per_trade:.2f} < min €{min_position}"

        # Approved
        return PlaybookVerdict.APPROVED, "Setup approved – within risk limits"

    def _enrich_with_llm_reasoning(self, entries: list[PlaybookEntry]) -> list[PlaybookEntry]:
        """Optionally enrich entries with LLM-generated reasoning summaries."""
        if not entries:
            return entries

        # Build context for LLM
        summaries_prompt = "Generate brief verdict reasoning (1-2 sentences each) for these setups:\n\n"
        for e in entries:
            summaries_prompt += (
                f"- {e.symbol} {e.timeframe}: verdict={e.verdict.value}, "
                f"direction={e.direction.value}, action={e.action.value}, "
                f"RR={e.reward_risk_ratio:.1f}\n"
            )
        summaries_prompt += "\nRespond with JSON: a list of {\"symbol\": \"...\", \"verdict_reasoning\": \"...\"}"

        try:
            raw = call_gemini(
                agent_name="risk_agent",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=summaries_prompt,
                model=self._config.get("model", "gemini-2.5-flash"),
                temperature=self._config.get("temperature", 0.0),
                max_output_tokens=self._config.get("max_output_tokens", 512),
            )

            # Parse reasoning
            cleaned = raw.strip()
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
            data = json.loads(cleaned)

            reasoning_map = {item["symbol"]: item["verdict_reasoning"] for item in data}

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

    @staticmethod
    def _load_portfolio() -> dict:
        """Load portfolio state from ``positions.json``."""
        path = PROJECT_ROOT / "positions.json"
        if not path.exists():
            logger.warning("positions.json not found — using empty portfolio")
            return {
                "portfolio_value_eur": 100.0,
                "cash_eur": 100.0,
                "open_positions": [],
                "closed_trades": [],
            }
        with open(path, encoding="utf-8") as fh:
            content = fh.read().strip()
            if not content:
                logger.warning("positions.json is empty — using defaults")
                return {
                    "portfolio_value_eur": 100.0,
                    "cash_eur": 100.0,
                    "open_positions": [],
                    "closed_trades": [],
                }
            return json.loads(content)
