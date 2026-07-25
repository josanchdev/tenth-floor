"""
Notion Signal Journal — auto-posts signals and resolves outcomes.

Uses the Notion REST API directly (not MCP, which is interactive-only).
Requires NOTION_INTEGRATION_TOKEN and NOTION_SIGNAL_DATABASE_ID in .env.

Setup:
  1. notion.so/my-integrations → create an internal integration → copy token
  2. Open your Signal Journal database in Notion → ··· → Connections → add integration
  3. Set NOTION_INTEGRATION_TOKEN and NOTION_SIGNAL_DATABASE_ID in .env

Both calls are silent no-ops when the token is unset — dev machines stay quiet.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from tenth_floor.data.models import PlaybookEntry

logger = logging.getLogger(__name__)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 10.0

_TOKEN_ENV = "NOTION_INTEGRATION_TOKEN"
_DATABASE_ENV = "NOTION_SIGNAL_DATABASE_ID"

# Status → Notion Outcome option
_STATUS_TO_OUTCOME = {
    "HIT_TP": "Won",
    "HIT_SL": "Lost",
    "EXPIRED": "Expired",
    "OPEN": "Open",
}

# Asset class normalisation (pipeline uses lowercase)
_ASSET_CLASS_LABEL = {
    "crypto": "Crypto",
    "equity": "Equity",
    "etf": "ETF",
    "commodity": "Commodity",
}


def _headers() -> dict[str, str] | None:
    token = os.getenv(_TOKEN_ENV, "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_signal_entry(
    entry: PlaybookEntry,
    *,
    signal_id: str,
    asset_class: str | None = None,
    macro_regime: str | None = None,
) -> str | None:
    """Create a Signal Journal page for a newly published signal.

    Returns the Notion page_id to be stored in SQLite, or None on failure.
    """
    hdrs = _headers()
    database_id = os.getenv(_DATABASE_ENV, "").strip()
    if not hdrs or not database_id:
        logger.debug("Notion journal: token or database ID not set, skipping")
        return None

    asset_class_label = _ASSET_CLASS_LABEL.get((asset_class or "").lower(), "Crypto")
    conviction_label = entry.conviction.value.capitalize()

    risk = entry.entry_price - entry.stop_loss
    reward = entry.take_profit - entry.entry_price
    risk_pct = (risk / entry.entry_price * 100) if entry.entry_price else 0
    reward_pct = (reward / entry.entry_price * 100) if entry.entry_price else 0

    page_content = _build_open_content(entry, risk_pct, reward_pct, macro_regime)

    payload: dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": {
            "Asset": {"title": [{"text": {"content": entry.symbol}}]},
            "Asset class": {"select": {"name": asset_class_label}},
            "Date": {"date": {"start": entry.report_date}},
            "Entry": {"number": entry.entry_price},
            "SL": {"number": entry.stop_loss},
            "TP": {"number": entry.take_profit},
            "Planned R:R": {"number": round(entry.reward_risk_ratio, 2)},
            "Conviction": {"select": {"name": conviction_label}},
            "Outcome": {"select": {"name": "Open"}},
            "Signal ID": {"rich_text": [{"text": {"content": signal_id}}]},
            "Signal reasoning": {"rich_text": [{"text": {"content": entry.rationale[:2000]}}]},
        },
        "children": _markdown_to_blocks(page_content),
    }

    try:
        resp = requests.post(f"{_NOTION_API}/pages", json=payload, headers=hdrs, timeout=_TIMEOUT)
        resp.raise_for_status()
        page_id: str = resp.json()["id"]
        logger.info("Notion: created journal page %s for %s", page_id, entry.symbol)
        return page_id
    except Exception:
        logger.exception("Notion create_signal_entry failed for %s — continuing", entry.symbol)
        return None


def update_signal_outcome(
    page_id: str,
    *,
    status: str,
    outcome_price: float | None = None,
    outcome_date: str | None = None,
    mae: float | None = None,
    mfe: float | None = None,
    entry_price: float | None = None,
) -> None:
    """Update a Signal Journal page when a signal resolves.

    Sets Outcome to Won/Lost/Expired and appends a post-mortem skeleton.
    """
    hdrs = _headers()
    if not hdrs:
        return

    outcome_label = _STATUS_TO_OUTCOME.get(status, "Open")

    # Build post-mortem skeleton
    lines = [f"Outcome: **{outcome_label}**"]
    if outcome_price is not None:
        lines.append(f"Exit price: {outcome_price:.4f}")
    if outcome_date:
        lines.append(f"Exit date: {outcome_date[:10]}")
    if entry_price and mae is not None:
        mae_pct = (entry_price - mae) / entry_price * 100
        lines.append(f"MAE: {mae:.4f}  ({mae_pct:.2f}% adverse from entry)")
    if entry_price and mfe is not None:
        mfe_pct = (mfe - entry_price) / entry_price * 100
        lines.append(f"MFE: {mfe:.4f}  ({mfe_pct:.2f}% favorable from entry)")
    lines.append("\n_Add your notes here._")

    post_mortem = "\n".join(lines)

    try:
        # Update properties
        resp = requests.patch(
            f"{_NOTION_API}/pages/{page_id}",
            json={
                "properties": {
                    "Outcome": {"select": {"name": outcome_label}},
                    "Post-mortem": {"rich_text": [{"text": {"content": post_mortem[:2000]}}]},
                }
            },
            headers=hdrs,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Notion: updated outcome for page %s → %s", page_id, outcome_label)
    except Exception:
        logger.exception("Notion update_signal_outcome failed for page %s — continuing", page_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_open_content(
    entry: PlaybookEntry,
    risk_pct: float,
    reward_pct: float,
    macro_regime: str | None,
) -> str:
    regime_line = f"**Macro regime:** {macro_regime}" if macro_regime else ""
    return f"""## Setup

| Field | Value |
|-------|-------|
| Entry | {entry.entry_price:.4f} |
| Stop | {entry.stop_loss:.4f}  ({risk_pct:.2f}% risk) |
| Target | {entry.take_profit:.4f}  ({reward_pct:.2f}% reward) |
| R:R | {entry.reward_risk_ratio:.2f} |
| Conviction | {entry.conviction.value.upper()} |
| Confidence | {entry.confidence_score * 100:.0f}% |
| Rank | #{entry.rank} |
{regime_line}

## Rationale

{entry.rationale}

## Post-mortem

_Trade is open. This section will be filled when the outcome is resolved._
"""


def _markdown_to_blocks(text: str) -> list[dict]:
    """Convert a simple markdown string to Notion block objects.

    Handles headings (##), paragraphs, and horizontal rules.
    Tables are passed as plain paragraphs since Notion REST doesn't support
    markdown table syntax.
    """
    blocks: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": stripped[3:]}}]},
            })
        elif stripped.startswith("---"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": stripped}}]},
            })
    return blocks
