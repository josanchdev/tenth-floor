"""
CLI entry point for the tweet auto-poster.

Usage::

    # Generate draft only (no posting) — safe to call from run.sh
    python -m crypto_swing_copilot.post_tweet --draft-only

    # Interactive: review draft and post
    python -m crypto_swing_copilot.post_tweet

    # Specific date
    python -m crypto_swing_copilot.post_tweet 2026-03-28
    python -m crypto_swing_copilot.post_tweet 2026-03-28 --draft-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="The Tenth Floor — Tweet Auto-Poster",
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Report date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="Generate draft without posting (safe for run.sh).",
    )
    args = parser.parse_args()

    report_date = args.date or date.today().isoformat()

    if args.draft_only:
        from crypto_swing_copilot.social.discord_draft import send_draft_to_discord
        from crypto_swing_copilot.social.tweet_drafter import TweetDrafter

        with TweetDrafter() as drafter:
            draft = drafter.draft(report_date)

        print(f"\nDraft generated for {report_date}:")
        for i, opt in enumerate(draft.drafts.options, 1):
            print(f"  Option {i} [{opt.tweet_type}]: {opt.text}")
        print(f"\nSaved to data/tweet_drafts/{report_date}.json")

        # Send to private Discord channel for easy copy-paste
        if send_draft_to_discord(draft):
            print("Draft sent to Discord #tweet-drafts channel")
    else:
        from crypto_swing_copilot.social.tweet_poster import TweetPoster

        with TweetPoster() as poster:
            posted = poster.post_interactive(report_date)

        sys.exit(0 if posted else 1)


if __name__ == "__main__":
    _cli_main()
