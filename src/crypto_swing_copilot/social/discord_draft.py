"""
Send tweet drafts to a private Discord channel for manual copy-paste posting.

Reads the draft JSON, formats it as a clean Discord embed, and sends it
to ``DISCORD_TWEET_WEBHOOK_URL``.  The owner copies the text straight
into X — no API cost.
"""

from __future__ import annotations

import logging
import os

import requests

from crypto_swing_copilot.social.tweet_drafter import TweetDraftFile

logger = logging.getLogger(__name__)

COLOR_PURPLE = 0xA855F7


def send_draft_to_discord(
    draft: TweetDraftFile,
    webhook_url: str | None = None,
) -> bool:
    """Format and send tweet draft options to a private Discord channel.

    Parameters
    ----------
    draft:
        The generated draft file with 1-2 tweet options.
    webhook_url:
        Override the webhook URL.  Defaults to ``DISCORD_TWEET_WEBHOOK_URL``.

    Returns
    -------
    bool
        ``True`` if the webhook call succeeded.
    """
    url = webhook_url or os.environ.get("DISCORD_TWEET_WEBHOOK_URL")
    if not url:
        logger.warning("DISCORD_TWEET_WEBHOOK_URL not set — skipping draft notification")
        return False

    embeds = []
    for i, opt in enumerate(draft.drafts.options, 1):
        # Main tweet in a code block so it's easy to select-all and copy
        body = f"```\n{opt.text}\n```"
        body += f"\n**Type:** {opt.tweet_type}  ·  **Chars:** {len(opt.text)}/280"

        if opt.thread:
            for j, t in enumerate(opt.thread, 1):
                body += f"\n\n**Thread {j}:**\n```\n{t}\n```"
                body += f"**Chars:** {len(t)}/280"

        embeds.append({
            "title": f"Option {i}" if len(draft.drafts.options) > 1 else "Tweet Draft",
            "description": body,
            "color": COLOR_PURPLE,
        })

    # Add reasoning as a small footer embed
    embeds.append({
        "description": f"*{draft.drafts.reasoning}*",
        "color": COLOR_PURPLE,
        "footer": {"text": f"Draft for {draft.report_date} · copy → paste → post"},
    })

    payload = {
        "username": "The Tenth Floor",
        "content": f"**🐦 Tweet Draft — {draft.report_date}**",
        "embeds": embeds,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Tweet draft sent to Discord  status=%d", resp.status_code)
        return True
    except requests.HTTPError as exc:
        logger.error(
            "Discord tweet webhook HTTP error  status=%d  body=%s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        return False
    except requests.RequestException as exc:
        logger.error("Discord tweet webhook failed: %s", exc)
        return False
