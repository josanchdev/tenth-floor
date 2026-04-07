"""
Python validation layer — sanity checks on LLM trade proposals.

This is NOT a judgment call. It's a safety net for when the LLM makes
a math error or hallucinates. If validation fails, the proposal is
rejected with a clear reason.

Checks:
  - No SHORT direction (spot only)
  - SL < entry < TP (basic directional sanity)
  - SL not more than 15% below entry (prevents absurd stops)
  - TP not more than 50% above entry (prevents fantasy targets)
  - R:R >= 1.5 hard floor (business integrity)
  - R:R math verification (recalculate from LLM's numbers)

Usage::

    from tenth_floor.validation import validate_proposal

    result = validate_proposal(proposal)
    if result.valid:
        # proceed
    else:
        logger.info("Rejected: %s — %s", proposal.symbol, result.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tenth_floor.data.models import TradeProposal

logger = logging.getLogger(__name__)

# Hard business rules
_MIN_RR: float = 1.5
_MAX_SL_PCT: float = 0.15   # SL not more than 15% below entry
_MAX_TP_PCT: float = 0.50   # TP not more than 50% above entry


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a TradeProposal."""

    valid: bool
    reason: str
    reward_risk_ratio: float


def validate_proposal(proposal: TradeProposal) -> ValidationResult:
    """Validate a TradeProposal for sanity.

    Parameters
    ----------
    proposal:
        A TradeProposal from TradeAnalyst.

    Returns
    -------
    ValidationResult
        Whether the proposal is valid and the computed R:R.
    """
    symbol = proposal.symbol

    # Skip proposals don't need validation
    if proposal.action.value == "skip":
        return ValidationResult(valid=True, reason="SKIP", reward_risk_ratio=0.0)

    # Rule 1: No SHORT
    if proposal.direction.value == "short":
        return ValidationResult(
            valid=False,
            reason=f"{symbol}: SHORT direction — spot only",
            reward_risk_ratio=0.0,
        )

    entry_mid = (proposal.entry_zone_low + proposal.entry_zone_high) / 2

    # Rule 2: SL < entry < TP
    if proposal.stop_loss >= entry_mid:
        return ValidationResult(
            valid=False,
            reason=f"{symbol}: SL ({proposal.stop_loss}) >= entry ({entry_mid})",
            reward_risk_ratio=0.0,
        )
    if proposal.take_profit <= entry_mid:
        return ValidationResult(
            valid=False,
            reason=f"{symbol}: TP ({proposal.take_profit}) <= entry ({entry_mid})",
            reward_risk_ratio=0.0,
        )

    # Rule 3: SL not too far below entry
    sl_distance_pct = (entry_mid - proposal.stop_loss) / entry_mid
    if sl_distance_pct > _MAX_SL_PCT:
        return ValidationResult(
            valid=False,
            reason=(
                f"{symbol}: SL {sl_distance_pct:.1%} below entry "
                f"(max {_MAX_SL_PCT:.0%})"
            ),
            reward_risk_ratio=0.0,
        )

    # Rule 4: TP not too far above entry
    tp_distance_pct = (proposal.take_profit - entry_mid) / entry_mid
    if tp_distance_pct > _MAX_TP_PCT:
        return ValidationResult(
            valid=False,
            reason=(
                f"{symbol}: TP {tp_distance_pct:.1%} above entry "
                f"(max {_MAX_TP_PCT:.0%})"
            ),
            reward_risk_ratio=0.0,
        )

    # Rule 5: R:R verification
    risk = entry_mid - proposal.stop_loss
    reward = proposal.take_profit - entry_mid
    computed_rr = round(reward / risk, 2) if risk > 0 else 0.0

    if computed_rr < _MIN_RR:
        return ValidationResult(
            valid=False,
            reason=(
                f"{symbol}: R:R {computed_rr:.2f} below minimum {_MIN_RR:.1f}"
            ),
            reward_risk_ratio=computed_rr,
        )

    # Rule 6: entry_zone_low < entry_zone_high
    if proposal.entry_zone_low >= proposal.entry_zone_high:
        return ValidationResult(
            valid=False,
            reason=(
                f"{symbol}: entry_zone_low ({proposal.entry_zone_low}) "
                f">= entry_zone_high ({proposal.entry_zone_high})"
            ),
            reward_risk_ratio=computed_rr,
        )

    return ValidationResult(
        valid=True,
        reason="passed",
        reward_risk_ratio=computed_rr,
    )
