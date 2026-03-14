"""Unit tests for SentimentFetcher — mocked HTTP, no real network calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from crypto_swing_copilot.data.models import RSSHeadline, SentimentSnapshot
from crypto_swing_copilot.data.sentiment import SentimentFetcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Minimal config so the fetcher doesn't need services.yaml on disk
_TEST_CONFIG: dict = {
    "fear_greed": {
        "url": "https://api.alternative.me/fng/",
        "limit": 7,
    },
    "rss_feeds": [
        {
            "name": "test_feed",
            "url": "https://example.com/rss",
            "max_items": 3,
        },
    ],
}

# Realistic F&G API response (trimmed to 3 entries for brevity)
_FG_API_RESPONSE: dict = {
    "name": "Fear and Greed Index",
    "data": [
        {"value": "72", "value_classification": "Greed", "timestamp": "1710374400"},
        {"value": "65", "value_classification": "Greed", "timestamp": "1710288000"},
        {"value": "58", "value_classification": "Neutral", "timestamp": "1710201600"},
    ],
}


@pytest.fixture
def fetcher() -> SentimentFetcher:
    """Return a SentimentFetcher with test config (no file I/O)."""
    return SentimentFetcher(config_override=_TEST_CONFIG)


# ---------------------------------------------------------------------------
# Fear & Greed tests
# ---------------------------------------------------------------------------


class TestFetchFearGreed:
    """Tests for fetch_fear_greed()."""

    def test_success(self, fetcher: SentimentFetcher) -> None:
        """Happy path: API returns valid data."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(_FG_API_RESPONSE).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            value, label, trend = fetcher.fetch_fear_greed()

        assert value == 72
        assert label == "Greed"
        assert trend == [72, 65, 58]

    def test_timeout_graceful_degradation(self, fetcher: SentimentFetcher) -> None:
        """API timeout → returns safe defaults, no exception raised."""
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            value, label, trend = fetcher.fetch_fear_greed()

        assert value == 50
        assert label == "Neutral"
        assert trend == []

    def test_malformed_json(self, fetcher: SentimentFetcher) -> None:
        """API returns invalid JSON → graceful degradation."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not json at all"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            value, label, trend = fetcher.fetch_fear_greed()

        assert value == 50
        assert label == "Neutral"
        assert trend == []

    def test_empty_data_array(self, fetcher: SentimentFetcher) -> None:
        """API returns empty data → graceful degradation."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"data": []}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            value, label, trend = fetcher.fetch_fear_greed()

        assert value == 50
        assert label == "Neutral"
        assert trend == []


# ---------------------------------------------------------------------------
# RSS tests
# ---------------------------------------------------------------------------


class TestFetchRSSHeadlines:
    """Tests for fetch_rss_headlines()."""

    def test_success_with_capping(self, fetcher: SentimentFetcher) -> None:
        """Feedparser returns 5 items but max_items=3 → only 3 headlines."""
        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.entries = [
            MagicMock(
                title=f"Headline {i}",
                link=f"https://example.com/{i}",
                published_parsed=(2026, 3, 14, 10, i, 0, 0, 73, 0),
            )
            for i in range(5)
        ]
        # feedparser entries need .get() support
        for entry in mock_feed.entries:
            entry.get = lambda key, default="", _e=entry: (
                getattr(_e, key, default) if hasattr(_e, key) else default
            )

        with patch("feedparser.parse", return_value=mock_feed):
            headlines = fetcher.fetch_rss_headlines()

        assert len(headlines) == 3  # capped from 5
        assert all(isinstance(h, RSSHeadline) for h in headlines)
        assert headlines[0].source == "test_feed"
        assert headlines[0].title == "Headline 0"

    def test_feed_failure_returns_empty(self, fetcher: SentimentFetcher) -> None:
        """Feedparser raises exception → empty list, no crash."""
        with patch("feedparser.parse", side_effect=Exception("network error")):
            headlines = fetcher.fetch_rss_headlines()

        assert headlines == []

    def test_no_feeds_configured(self) -> None:
        """No RSS feeds in config → empty list."""
        cfg = {**_TEST_CONFIG, "rss_feeds": []}
        fetcher = SentimentFetcher(config_override=cfg)
        headlines = fetcher.fetch_rss_headlines()
        assert headlines == []


# ---------------------------------------------------------------------------
# Integration: fetch_snapshot
# ---------------------------------------------------------------------------


class TestFetchSnapshot:
    """Tests for fetch_snapshot() — combines both sources."""

    def test_returns_valid_model(self, fetcher: SentimentFetcher) -> None:
        """Combined fetch returns a valid SentimentSnapshot."""
        # Mock F&G
        mock_fg_resp = MagicMock()
        mock_fg_resp.read.return_value = json.dumps(_FG_API_RESPONSE).encode()
        mock_fg_resp.__enter__ = lambda s: s
        mock_fg_resp.__exit__ = MagicMock(return_value=False)

        # Mock RSS
        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.entries = [
            MagicMock(title="BTC hits ATH", link="https://example.com/1", published_parsed=None)
        ]
        mock_feed.entries[0].get = lambda key, default="", _e=mock_feed.entries[0]: (
            getattr(_e, key, default) if hasattr(_e, key) else default
        )

        with (
            patch("urllib.request.urlopen", return_value=mock_fg_resp),
            patch("feedparser.parse", return_value=mock_feed),
        ):
            snapshot = fetcher.fetch_snapshot()

        assert isinstance(snapshot, SentimentSnapshot)
        assert snapshot.fear_greed_value == 72
        assert snapshot.fear_greed_label == "Greed"
        assert snapshot.fear_greed_trend == [72, 65, 58]
        assert len(snapshot.headlines) == 1
        assert snapshot.headlines[0].title == "BTC hits ATH"
        assert isinstance(snapshot.fetched_at, datetime)

    def test_both_sources_down(self, fetcher: SentimentFetcher) -> None:
        """Both APIs fail → still returns a valid (degraded) snapshot."""
        with (
            patch("urllib.request.urlopen", side_effect=TimeoutError("down")),
            patch("feedparser.parse", side_effect=Exception("down")),
        ):
            snapshot = fetcher.fetch_snapshot()

        assert isinstance(snapshot, SentimentSnapshot)
        assert snapshot.fear_greed_value == 50
        assert snapshot.fear_greed_label == "Neutral"
        assert snapshot.fear_greed_trend == []
        assert snapshot.headlines == []
