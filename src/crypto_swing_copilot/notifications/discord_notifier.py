"""
DiscordNotifier — posts the daily playbook embed to a Discord webhook.

One consolidated embed per daily run.  Each approved signal is a single
embed field with a multi-line value.  Zero-signal days still post (never
go silent).

Usage::

    from crypto_swing_copilot.notifications.discord_notifier import DiscordNotifier

    notifier = DiscordNotifier()
    notifier.post(approved_entries, open_count=5)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests

from crypto_swing_copilot.data.models import PlaybookEntry

logger = logging.getLogger(__name__)

COLOR_GREEN = 0x00FF88
COLOR_GREY = 0x888888
COLOR_RED = 0xFF4444
COLOR_GOLD = 0xFFD700


class DiscordNotifier:
    """Posts daily signal embeds to Discord via webhook.

    Parameters
    ----------
    webhook_url:
        Override the webhook URL.  Defaults to ``DISCORD_WEBHOOK_URL`` env var.
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        if not self._webhook_url:
            logger.warning(
                "DISCORD_WEBHOOK_URL not set — DiscordNotifier will no-op"
            )

    def post(
        self,
        entries: list[PlaybookEntry],
        open_count: int,
        report_date: str | None = None,
    ) -> bool:
        """Post the daily playbook embed to Discord.

        Parameters
        ----------
        entries:
            Approved ``PlaybookEntry`` records for today's run.
        open_count:
            Total PENDING + OPEN signals in the database.
        report_date:
            ISO date string for the embed title.  Defaults to today (UTC).

        Returns
        -------
        bool
            ``True`` if the webhook call succeeded, ``False`` otherwise.
        """
        if not self._webhook_url:
            logger.warning("No webhook URL configured — skipping Discord post")
            return False

        if report_date is None:
            report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        embed = self._build_embed(entries, open_count, report_date)
        return self._send({"embeds": [embed]})

    def _build_embed(
        self,
        entries: list[PlaybookEntry],
        open_count: int,
        report_date: str,
    ) -> dict:
        """Build the Discord embed payload."""
        has_signals = len(entries) > 0

        embed: dict = {
            "title": f"\U0001f51f THE TENTH FLOOR \u2014 {report_date}",
            "color": COLOR_GREEN if has_signals else COLOR_GREY,
            "footer": {
                "text": f"Open signals in DB: {open_count}  |  Powered by The Tenth Floor AI",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if has_signals:
            embed["fields"] = [self._signal_field(e) for e in entries]
        else:
            embed["description"] = (
                "No setups met our criteria today. "
                "We only publish when the evidence is overwhelming — "
                "silence means the market doesn't offer a clear edge right now."
            )

        return embed

    @staticmethod
    def _signal_field(entry: PlaybookEntry) -> dict:
        """Format one approved signal as a Discord embed field."""
        risk_pct = entry.suggested_risk_pct * 100
        value = (
            f"Entry: {entry.entry_zone_low} \u2013 {entry.entry_zone_high}\n"
            f"Stop: {entry.stop_loss}  |  Target: {entry.take_profit}\n"
            f"R:R: {entry.reward_risk_ratio}  \u00b7  Risk: {risk_pct:.0f}%\n"
            f"Rationale: {entry.strategy_rationale}"
        )
        return {
            "name": f"{entry.symbol} {entry.timeframe.upper()} \u00b7 {entry.direction.value.upper()} \u00b7 {entry.conviction.upper()}",
            "value": value,
            "inline": False,
        }

    def post_outcome(self, signal: dict) -> bool:
        """Post a signal resolution update to Discord.

        Parameters
        ----------
        signal:
            Dict with at least: pair, status, entry_high, stop_loss,
            take_profit, outcome_price, timeframe.

        Returns
        -------
        bool
            ``True`` if the webhook call succeeded.
        """
        if not self._webhook_url:
            return False

        status = signal.get("status", "")
        pair = signal.get("pair", "???")
        entry = signal.get("entry_high", 0)
        outcome_price = signal.get("outcome_price")
        timeframe = signal.get("timeframe", "")

        if status == "HIT_TP":
            color = COLOR_GREEN
            icon = "\u2705"  # ✅
            label = "TARGET HIT"
            if entry and outcome_price:
                pct = (outcome_price - entry) / entry * 100
                detail = f"+{pct:.1f}% at {outcome_price}"
            else:
                detail = f"at {outcome_price}"
        elif status == "HIT_SL":
            color = COLOR_RED
            icon = "\U0001f6d1"  # 🛑
            label = "STOPPED OUT"
            if entry and outcome_price:
                pct = (outcome_price - entry) / entry * 100
                detail = f"{pct:.1f}% at {outcome_price}"
            else:
                detail = f"at {outcome_price}"
        elif status == "EXPIRED":
            color = COLOR_GOLD
            icon = "\u23f0"  # ⏰
            label = "EXPIRED"
            detail = "14 days — no resolution"
        else:
            return False

        embed = {
            "title": f"{icon} {pair} {timeframe.upper()} — {label}",
            "description": detail,
            "color": color,
            "footer": {
                "text": "Powered by The Tenth Floor AI",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send({"embeds": [embed]})

    def _send(self, payload: dict) -> bool:
        """Send a JSON payload to the Discord webhook.

        Sleeps 1 second between calls if ever extended to multiple sends.
        """
        try:
            resp = requests.post(
                self._webhook_url,  # type: ignore[arg-type]
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Discord embed posted  status=%d", resp.status_code)
            return True
        except requests.HTTPError as exc:
            logger.error(
                "Discord webhook HTTP error  status=%d  body=%s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Discord webhook request failed: %s", exc)
            return False
