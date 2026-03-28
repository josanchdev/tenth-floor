"""Unit tests for TweetDrafter — LLM-based tweet draft generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crypto_swing_copilot.social.tweet_drafter import (
    TweetDrafter,
    TweetDraftFile,
    TweetDraftResponse,
    TweetOption,
    _fg_label,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


MOCK_LLM_RESPONSE = json.dumps({
    "options": [
        {
            "text": "26 pairs scanned. 22 killed at trend gate. 0 approved. The AI said no today.",
            "tweet_type": "funnel_report",
            "thread": [],
        }
    ],
    "reasoning": "Zero-signal day — funnel report is the content.",
})


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestTweetModels:
    def test_tweet_option_valid(self) -> None:
        opt = TweetOption(text="Hello world", tweet_type="philosophy")
        assert opt.text == "Hello world"
        assert opt.thread == []

    def test_tweet_option_with_thread(self) -> None:
        opt = TweetOption(
            text="Main tweet", tweet_type="signal_day",
            thread=["Thread reply 1"],
        )
        assert len(opt.thread) == 1

    def test_tweet_draft_response(self) -> None:
        resp = TweetDraftResponse(
            options=[TweetOption(text="Test", tweet_type="philosophy")],
            reasoning="Testing",
        )
        assert len(resp.options) == 1

    def test_tweet_draft_response_many_options(self) -> None:
        resp = TweetDraftResponse(
            options=[
                TweetOption(text=f"Opt {i}", tweet_type="philosophy")
                for i in range(4)
            ],
            reasoning="LLM returned extra",
        )
        assert len(resp.options) == 4  # model accepts, code trims to 2

    def test_tweet_draft_file(self) -> None:
        draft = TweetDraftFile(
            report_date="2026-03-28",
            generated_at="2026-03-28T12:00:00",
            pipeline_context={"report_date": "2026-03-28"},
            drafts=TweetDraftResponse(
                options=[TweetOption(text="X", tweet_type="funnel_report")],
                reasoning="Test",
            ),
        )
        assert draft.posted is False
        assert draft.image_paths == []


# ---------------------------------------------------------------------------
# Fear & Greed label
# ---------------------------------------------------------------------------

class TestFGLabel:
    def test_extreme_fear(self) -> None:
        assert _fg_label(10) == "Extreme Fear"

    def test_fear(self) -> None:
        assert _fg_label(35) == "Fear"

    def test_neutral(self) -> None:
        assert _fg_label(50) == "Neutral"

    def test_greed(self) -> None:
        assert _fg_label(65) == "Greed"

    def test_extreme_greed(self) -> None:
        assert _fg_label(90) == "Extreme Greed"


# ---------------------------------------------------------------------------
# TweetDrafter tests
# ---------------------------------------------------------------------------

class TestTweetDrafter:
    @patch("crypto_swing_copilot.social.tweet_drafter.call_llm", return_value=MOCK_LLM_RESPONSE)
    def test_draft_generates_file(self, mock_llm: MagicMock, db_path: Path, tmp_path: Path) -> None:
        with patch("crypto_swing_copilot.social.tweet_drafter.DATA_DIR", tmp_path):
            drafter = TweetDrafter(db_path=db_path)
            draft = drafter.draft("2026-03-28")
            drafter.close()

        assert draft.report_date == "2026-03-28"
        assert len(draft.drafts.options) == 1
        assert draft.drafts.options[0].tweet_type == "funnel_report"
        assert draft.posted is False

        # Check file was saved
        saved = tmp_path / "tweet_drafts" / "2026-03-28.json"
        assert saved.exists()
        data = json.loads(saved.read_text())
        assert data["report_date"] == "2026-03-28"

    @patch("crypto_swing_copilot.social.tweet_drafter.call_llm", return_value=MOCK_LLM_RESPONSE)
    def test_draft_truncates_long_tweets(self, mock_llm: MagicMock, db_path: Path, tmp_path: Path) -> None:
        long_response = json.dumps({
            "options": [{
                "text": "A" * 300,
                "tweet_type": "philosophy",
                "thread": ["B" * 300],
            }],
            "reasoning": "Test truncation",
        })
        mock_llm.return_value = long_response

        with patch("crypto_swing_copilot.social.tweet_drafter.DATA_DIR", tmp_path):
            drafter = TweetDrafter(db_path=db_path)
            draft = drafter.draft("2026-03-28")
            drafter.close()

        assert len(draft.drafts.options[0].text) == 280
        assert len(draft.drafts.options[0].thread[0]) == 280

    def test_context_manager(self, db_path: Path) -> None:
        with TweetDrafter(db_path=db_path) as drafter:
            assert drafter is not None

    def test_days_since_last_signal_none(self, db_path: Path) -> None:
        drafter = TweetDrafter(db_path=db_path)
        result = drafter._days_since_last_signal("2026-03-28")
        assert result is None
        drafter.close()

    def test_recent_track_record_empty(self, db_path: Path) -> None:
        drafter = TweetDrafter(db_path=db_path)
        record = drafter._recent_track_record()
        assert record["total_published"] == 0
        assert record["hit_tp"] == 0
        drafter.close()
