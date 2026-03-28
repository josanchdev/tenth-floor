"""
TweetPoster — interactive CLI for reviewing and posting tweet drafts to X.

Loads drafts from ``data/tweet_drafts/``, displays them for review, and
posts approved tweets via the X API (Tweepy).

Usage::

    poster = TweetPoster()
    poster.post_interactive()  # shows draft, waits for approval
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from crypto_swing_copilot.config import DATA_DIR
from crypto_swing_copilot.social.tweet_drafter import TweetDraftFile, TweetOption

logger = logging.getLogger(__name__)


class TweetPoster:
    """Posts tweet drafts to X with interactive approval."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._drafts_dir = DATA_DIR / "tweet_drafts"

        # Lazy DB import
        from crypto_swing_copilot.db.signal_logger import SignalLogger
        self._db = SignalLogger(db_path)

    def _init_tweepy(self):  # noqa: ANN202
        """Initialize Tweepy v2 client from env vars."""
        try:
            import tweepy
        except ImportError as exc:
            logger.error("tweepy not installed. Run: pip install tweepy")
            raise SystemExit(1) from exc

        api_key = os.environ.get("X_API_KEY")
        api_secret = os.environ.get("X_API_SECRET")
        access_token = os.environ.get("X_ACCESS_TOKEN")
        access_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")

        if not all([api_key, api_secret, access_token, access_secret]):
            missing = [
                k for k, v in {
                    "X_API_KEY": api_key, "X_API_SECRET": api_secret,
                    "X_ACCESS_TOKEN": access_token, "X_ACCESS_TOKEN_SECRET": access_secret,
                }.items() if not v
            ]
            logger.error("Missing X API env vars: %s", ", ".join(missing))
            raise SystemExit(1)  # noqa: B904 — intentional exit, not a re-raise

        return tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )

    def post_interactive(self, report_date: str | None = None) -> bool:
        """Interactive CLI flow: show draft, get approval, post.

        Returns
        -------
        bool
            True if a tweet was posted, False otherwise.
        """
        if report_date is None:
            report_date = date.today().isoformat()

        draft = self._load_draft(report_date)
        if draft is None:
            print(f"\nNo draft found for {report_date}.")
            print("Run with --draft-only first, or: python -m crypto_swing_copilot.post_tweet --draft-only")
            return False

        if draft.posted:
            print(f"\nDraft for {report_date} was already posted.")
            return False

        # Check for duplicate in DB
        options = draft.drafts.options
        print(f"\n{'=' * 60}")
        print(f"  Tweet Draft — {report_date}")
        print(f"{'=' * 60}")

        for i, opt in enumerate(options, 1):
            char_count = len(opt.text)
            print(f"\n  Option {i} [{opt.tweet_type}]:")
            print(f"  {'─' * 50}")
            print(f"  {opt.text}")
            print(f"  ({char_count}/280 chars)")
            if opt.thread:
                for j, t in enumerate(opt.thread, 1):
                    print(f"    → Thread {j}: {t}")
                    print(f"      ({len(t)}/280 chars)")

        print(f"\n  Reasoning: {draft.drafts.reasoning}")
        print(f"{'=' * 60}")

        # Get user selection
        if len(options) == 1:
            choice = input("\nPost this tweet? (y/n/e to edit): ").strip().lower()
            if choice == "n":
                print("Skipped.")
                return False
            selected = options[0]
            if choice == "e":
                selected = self._edit_tweet(selected)
            elif choice != "y":
                print("Invalid choice. Skipped.")
                return False
        else:
            choice = input(f"\nSelect option (1-{len(options)}), 'e' to edit, 'n' to skip: ").strip().lower()
            if choice == "n":
                print("Skipped.")
                return False
            if choice == "e":
                idx = input(f"Which option to edit? (1-{len(options)}): ").strip()
                try:
                    selected = options[int(idx) - 1]
                except (ValueError, IndexError):
                    print("Invalid selection. Skipped.")
                    return False
                selected = self._edit_tweet(selected)
            else:
                try:
                    selected = options[int(choice) - 1]
                except (ValueError, IndexError):
                    print("Invalid selection. Skipped.")
                    return False

            confirm = input("\nPost this tweet? (y/n): ").strip().lower()
            if confirm != "y":
                print("Skipped.")
                return False

        # Post to X
        client = self._init_tweepy()
        tweet_id = self._post_tweet(client, selected)

        if tweet_id:
            # Log to DB
            self._db.log_tweet(
                tweet_id=tweet_id,
                report_date=report_date,
                tweet_text=selected.text,
                tweet_type=selected.tweet_type,
                thread_tweets=selected.thread if selected.thread else None,
                draft_file=str(self._drafts_dir / f"{report_date}.json"),
            )

            # Mark draft as posted
            draft_path = self._drafts_dir / f"{report_date}.json"
            draft_data = json.loads(draft_path.read_text(encoding="utf-8"))
            draft_data["posted"] = True
            draft_path.write_text(
                json.dumps(draft_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            print(f"\nPosted! https://x.com/TenthFloorHQ/status/{tweet_id}")
            return True

        return False

    def _load_draft(self, report_date: str) -> TweetDraftFile | None:
        """Load draft for a specific date."""
        path = self._drafts_dir / f"{report_date}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TweetDraftFile.model_validate(data)

    def _edit_tweet(self, option: TweetOption) -> TweetOption:
        """Let the user edit tweet text inline."""
        print(f"\nCurrent text ({len(option.text)}/280):")
        print(f"  {option.text}")
        new_text = input("\nEnter new text (or press Enter to keep): ").strip()
        if not new_text:
            return option
        if len(new_text) > 280:
            print(f"WARNING: {len(new_text)} chars exceeds 280 limit. Truncating.")
            new_text = new_text[:280]
        return TweetOption(
            text=new_text, tweet_type=option.tweet_type, thread=option.thread,
        )

    def _post_tweet(self, client, selected: TweetOption) -> str | None:
        """Post to X. Returns tweet ID."""
        try:
            response = client.create_tweet(text=selected.text)
            tweet_id = str(response.data["id"])
            logger.info("Tweet posted  id=%s", tweet_id)

            # Post thread if present
            if selected.thread:
                self._post_thread(client, tweet_id, selected.thread)

            return tweet_id
        except Exception as exc:
            logger.error("Failed to post tweet: %s", exc)
            print(f"\nError posting tweet: {exc}")
            return None

    def _post_thread(self, client, first_tweet_id: str, thread_tweets: list[str]) -> None:
        """Reply chain: each tweet replies to the previous one."""
        prev_id = first_tweet_id
        for i, text in enumerate(thread_tweets, 1):
            try:
                response = client.create_tweet(
                    text=text, in_reply_to_tweet_id=prev_id,
                )
                prev_id = str(response.data["id"])
                logger.info("Thread reply %d posted  id=%s", i, prev_id)
            except Exception as exc:
                logger.error("Failed to post thread reply %d: %s", i, exc)
                print(f"\nWarning: Thread reply {i} failed: {exc}")
                break

    def close(self) -> None:
        """Close the DB connection."""
        self._db.close()

    def __enter__(self) -> TweetPoster:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
