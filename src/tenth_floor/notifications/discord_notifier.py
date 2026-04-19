"""
Discord webhook notifier — mirrors the daily playbook off-box.

One embed per PUBLISHED signal. Silent on empty days, no-op when
``DISCORD_WEBHOOK_URL`` is unset (so dev machines stay quiet).
"""

from __future__ import annotations

import logging
import os

import requests

from tenth_floor.data.models import PlaybookEntry

logger = logging.getLogger(__name__)

_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
_TIMEOUT = 10.0

# Purple family to match the dashboard
_COLOR_HIGH = 0x7B5CFF
_COLOR_STANDARD = 0x5B4A9E


def publish_playbook(
    entries: list[PlaybookEntry],
    *,
    report_date: str,
    macro_regime: str | None = None,
) -> None:
    """Send one embed per entry to the configured webhook.

    Silent no-op when the webhook URL is unset or the entries list is
    empty. Errors are logged but never raised — a failed Discord post
    must not abort the pipeline.
    """
    webhook_url = os.getenv(_WEBHOOK_ENV, "").strip()
    if not webhook_url:
        logger.debug("Discord notifier: %s not set, skipping", _WEBHOOK_ENV)
        return
    if not entries:
        logger.debug("Discord notifier: no entries to publish")
        return

    header = f"**The Tenth Floor — {report_date}**"
    if macro_regime:
        header += f"  ·  macro: `{macro_regime}`"
    header += f"  ·  {len(entries)} signal{'s' if len(entries) != 1 else ''}"

    embeds = [_build_embed(entry) for entry in entries]

    try:
        resp = requests.post(
            webhook_url,
            json={"content": header, "embeds": embeds},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Discord: posted %d signal embed(s)", len(entries))
    except Exception:
        logger.exception("Discord post failed — continuing")


def _build_embed(entry: PlaybookEntry) -> dict:
    risk = entry.entry_price - entry.stop_loss
    reward = entry.take_profit - entry.entry_price
    risk_pct = (risk / entry.entry_price * 100) if entry.entry_price else 0
    reward_pct = (reward / entry.entry_price * 100) if entry.entry_price else 0

    color = _COLOR_HIGH if entry.conviction.value == "high" else _COLOR_STANDARD

    fields = [
        {"name": "Entry",  "value": f"`{entry.entry_price:.4f}`",  "inline": True},
        {"name": "Stop",   "value": f"`{entry.stop_loss:.4f}`  ({risk_pct:+.2f}%)", "inline": True},
        {"name": "Target", "value": f"`{entry.take_profit:.4f}`  ({reward_pct:+.2f}%)", "inline": True},
        {"name": "R:R",        "value": f"{entry.reward_risk_ratio:.2f}", "inline": True},
        {"name": "Conviction", "value": entry.conviction.value.upper(), "inline": True},
        {"name": "Confidence", "value": f"{entry.confidence_score * 100:.0f}%", "inline": True},
    ]

    description = entry.rationale.strip() or "_(no rationale)_"
    if len(description) > 1800:
        description = description[:1797] + "…"

    return {
        "title": f"{entry.symbol}  ·  {entry.direction.value.upper()}",
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": f"{entry.timeframe}  ·  rank #{entry.rank}"},
    }
