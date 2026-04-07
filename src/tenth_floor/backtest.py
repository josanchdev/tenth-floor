"""
Historical replay backtester — PLACEHOLDER for Phase 3.

The V3 backtester replayed deterministic gates without LLM calls. In the
Phase 1.5 AI-first architecture, the LLM makes all trading decisions, so
a meaningful backtester requires LLM-in-the-loop replay.

Phase 3 will implement:
- LLM-in-the-loop replay against historical OHLCV
- Compare LLM-chosen levels vs actual outcomes
- Signal quality metrics across time periods

For now, this module is a stub that raises NotImplementedError.
"""

from __future__ import annotations


def replay(**kwargs: object) -> list:
    """Placeholder — backtester needs redesign for AI-first architecture."""
    raise NotImplementedError(
        "Backtester not yet implemented for Phase 1.5 AI-first architecture. "
        "See ROADMAP.md Phase 3 for the plan."
    )


def main(argv: list[str] | None = None) -> None:
    print("Backtester not yet implemented for AI-first architecture.")
    print("See ROADMAP.md Phase 3 for the plan.")


if __name__ == "__main__":
    main()
