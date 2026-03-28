"""Unit tests for TweetPoster and Discord draft notifier."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crypto_swing_copilot.social.discord_draft import send_draft_to_discord
from crypto_swing_copilot.social.tweet_drafter import (
    TweetDraftFile,
    TweetDraftResponse,
    TweetOption,
)
from crypto_swing_copilot.social.tweet_poster import TweetPoster

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def drafts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tweet_drafts"
    d.mkdir()
    return d


def _make_draft(report_date: str = "2026-03-28", posted: bool = False) -> dict:
    return {
        "report_date": report_date,
        "generated_at": "2026-03-28T12:00:00",
        "pipeline_context": {"report_date": report_date},
        "drafts": {
            "options": [{
                "text": "26 pairs. 0 signals. The AI said no.",
                "tweet_type": "funnel_report",
                "thread": [],
            }],
            "reasoning": "Zero signal day.",
        },
        "image_paths": [],
        "posted": posted,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTweetPoster:
    def test_load_draft_not_found(self, db_path: Path, drafts_dir: Path) -> None:
        with patch("crypto_swing_copilot.social.tweet_poster.DATA_DIR", drafts_dir.parent):
            poster = TweetPoster(db_path=db_path)
            result = poster._load_draft("2099-01-01")
            poster.close()
        assert result is None

    def test_load_draft_valid(self, db_path: Path, drafts_dir: Path) -> None:
        draft_data = _make_draft()
        (drafts_dir / "2026-03-28.json").write_text(json.dumps(draft_data))

        with patch("crypto_swing_copilot.social.tweet_poster.DATA_DIR", drafts_dir.parent):
            poster = TweetPoster(db_path=db_path)
            result = poster._load_draft("2026-03-28")
            poster.close()

        assert result is not None
        assert isinstance(result, TweetDraftFile)
        assert result.report_date == "2026-03-28"

    def test_post_interactive_no_draft(self, db_path: Path, drafts_dir: Path, capsys) -> None:
        with patch("crypto_swing_copilot.social.tweet_poster.DATA_DIR", drafts_dir.parent):
            poster = TweetPoster(db_path=db_path)
            result = poster.post_interactive("2099-01-01")
            poster.close()

        assert result is False
        captured = capsys.readouterr()
        assert "No draft found" in captured.out

    def test_post_interactive_already_posted(self, db_path: Path, drafts_dir: Path, capsys) -> None:
        draft_data = _make_draft(posted=True)
        (drafts_dir / "2026-03-28.json").write_text(json.dumps(draft_data))

        with patch("crypto_swing_copilot.social.tweet_poster.DATA_DIR", drafts_dir.parent):
            poster = TweetPoster(db_path=db_path)
            result = poster.post_interactive("2026-03-28")
            poster.close()

        assert result is False
        captured = capsys.readouterr()
        assert "already posted" in captured.out

    def test_post_interactive_user_skips(self, db_path: Path, drafts_dir: Path) -> None:
        draft_data = _make_draft()
        (drafts_dir / "2026-03-28.json").write_text(json.dumps(draft_data))

        with patch("crypto_swing_copilot.social.tweet_poster.DATA_DIR", drafts_dir.parent):
            with patch("builtins.input", return_value="n"):
                poster = TweetPoster(db_path=db_path)
                result = poster.post_interactive("2026-03-28")
                poster.close()

        assert result is False

    def test_edit_tweet_keep(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        opt = TweetOption(text="Original", tweet_type="philosophy")

        with patch("builtins.input", return_value=""):
            result = poster._edit_tweet(opt)

        assert result.text == "Original"
        poster.close()

    def test_edit_tweet_change(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        opt = TweetOption(text="Original", tweet_type="philosophy")

        with patch("builtins.input", return_value="New text here"):
            result = poster._edit_tweet(opt)

        assert result.text == "New text here"
        poster.close()

    def test_edit_tweet_truncates(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        opt = TweetOption(text="Original", tweet_type="philosophy")

        with patch("builtins.input", return_value="X" * 300):
            result = poster._edit_tweet(opt)

        assert len(result.text) == 280
        poster.close()

    def test_context_manager(self, db_path: Path) -> None:
        with TweetPoster(db_path=db_path) as poster:
            assert poster is not None

    def test_post_tweet_error(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        mock_client = MagicMock()
        mock_client.create_tweet.side_effect = Exception("API error")

        opt = TweetOption(text="Test", tweet_type="philosophy")
        result = poster._post_tweet(mock_client, opt)

        assert result is None
        poster.close()

    def test_post_tweet_success(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        mock_client = MagicMock()
        mock_client.create_tweet.return_value = MagicMock(data={"id": "12345"})

        opt = TweetOption(text="Test", tweet_type="philosophy")
        result = poster._post_tweet(mock_client, opt)

        assert result == "12345"
        poster.close()

    def test_post_thread(self, db_path: Path) -> None:
        poster = TweetPoster(db_path=db_path)
        mock_client = MagicMock()
        mock_client.create_tweet.return_value = MagicMock(data={"id": "99999"})

        poster._post_thread(mock_client, "12345", ["Reply 1", "Reply 2"])

        assert mock_client.create_tweet.call_count == 2
        # First reply should be to original tweet
        mock_client.create_tweet.assert_any_call(
            text="Reply 1", in_reply_to_tweet_id="12345",
        )
        poster.close()


# ---------------------------------------------------------------------------
# Discord draft notifier
# ---------------------------------------------------------------------------

def _make_draft_obj(with_thread: bool = False) -> TweetDraftFile:
    thread = ["Thread reply here"] if with_thread else []
    return TweetDraftFile(
        report_date="2026-03-28",
        generated_at="2026-03-28T12:00:00",
        pipeline_context={"report_date": "2026-03-28"},
        drafts=TweetDraftResponse(
            options=[TweetOption(
                text="26 pairs. 0 signals. The AI said no.",
                tweet_type="funnel_report",
                thread=thread,
            )],
            reasoning="Zero signal day.",
        ),
    )


class TestDiscordDraft:
    def test_no_webhook_url_returns_false(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = send_draft_to_discord(_make_draft_obj())
        assert result is False

    def test_explicit_url_sends(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.raise_for_status = MagicMock()

        with patch("crypto_swing_copilot.social.discord_draft.requests.post", return_value=mock_resp) as mock_post:
            result = send_draft_to_discord(_make_draft_obj(), webhook_url="https://example.com/hook")

        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert "2026-03-28" in payload["content"]
        assert len(payload["embeds"]) == 2  # 1 option + 1 reasoning

    def test_with_thread_includes_thread_text(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("crypto_swing_copilot.social.discord_draft.requests.post", return_value=mock_resp) as mock_post:
            send_draft_to_discord(_make_draft_obj(with_thread=True), webhook_url="https://example.com/hook")

        payload = mock_post.call_args[1]["json"]
        option_embed = payload["embeds"][0]
        assert "Thread 1" in option_embed["description"]

    def test_http_error_returns_false(self) -> None:
        import requests as req

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError(
            response=MagicMock(status_code=400, text="Bad request"),
        )

        with patch("crypto_swing_copilot.social.discord_draft.requests.post", return_value=mock_resp):
            result = send_draft_to_discord(_make_draft_obj(), webhook_url="https://example.com/hook")

        assert result is False

    def test_env_var_fallback(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"DISCORD_TWEET_WEBHOOK_URL": "https://env.com/hook"}):
            with patch("crypto_swing_copilot.social.discord_draft.requests.post", return_value=mock_resp) as mock_post:
                result = send_draft_to_discord(_make_draft_obj())

        assert result is True
        assert mock_post.call_args[0][0] == "https://env.com/hook"
