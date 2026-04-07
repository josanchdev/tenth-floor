"""Tests for DiscordNotifier — embed construction and webhook posting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tenth_floor.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SignalDirection,
)
from tenth_floor.notifications.discord_notifier import (
    COLOR_GOLD,
    COLOR_GREEN,
    COLOR_GREY,
    COLOR_RED,
    DiscordNotifier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides) -> PlaybookEntry:
    """Build a minimal approved PlaybookEntry with sensible defaults."""
    defaults = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "report_date": "2026-03-20",
        "verdict": PlaybookVerdict.APPROVED,
        "verdict_reasoning": "All criteria met",
        "direction": SignalDirection.LONG,
        "entry_zone_low": 62_000.0,
        "entry_zone_high": 63_000.0,
        "stop_loss": 60_500.0,
        "take_profit": 66_000.0,
        "reward_risk_ratio": 2.0,
        "confidence_score": 0.82,
        "conviction": "high",
        "suggested_risk_pct": 0.02,
        "rationale": "EMA golden cross + RSI bounce",
        "rank": 1,
    }
    defaults.update(overrides)
    return PlaybookEntry(**defaults)


def _mock_response(status_code: int = 204, text: str = "") -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture()
def notifier():
    """Notifier with a fake webhook URL."""
    return DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/token")


@pytest.fixture()
def notifier_no_url():
    """Notifier with no webhook URL configured."""
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("DISCORD_WEBHOOK_URL", None)
        return DiscordNotifier()


# ---------------------------------------------------------------------------
# Embed construction
# ---------------------------------------------------------------------------

class TestBuildEmbed:
    """Tests for embed construction logic."""

    def test_embed_with_signals(self, notifier):
        entries = [_make_entry(), _make_entry(symbol="ETHUSDT", rank=2)]
        embed = notifier._build_embed(entries, open_count=7, report_date="2026-03-20")

        assert "THE TENTH FLOOR" in embed["title"]
        assert "2026-03-20" in embed["title"]
        assert embed["color"] == COLOR_GREEN
        assert len(embed["fields"]) == 2
        assert "Open signals in DB: 7" in embed["footer"]["text"]
        assert "description" not in embed

    def test_embed_zero_signals(self, notifier):
        embed = notifier._build_embed([], open_count=3, report_date="2026-03-20")

        assert embed["color"] == COLOR_GREY
        assert "No setups met our criteria today" in embed["description"]
        assert "fields" not in embed
        assert "Open signals in DB: 3" in embed["footer"]["text"]

    def test_signal_field_formatting(self, notifier):
        entry = _make_entry()
        field = notifier._signal_field(entry)

        assert field["name"] == "BTCUSDT 4H \u00b7 LONG \u00b7 HIGH"
        assert "Entry: 62000.0" in field["value"]
        assert "63000.0" in field["value"]
        assert "Stop: 60500.0" in field["value"]
        assert "Target: 66000.0" in field["value"]
        assert "R:R: 2.0" in field["value"]
        assert "Risk: 2%" in field["value"]
        assert "EMA golden cross" in field["value"]
        assert field["inline"] is False

    def test_signal_field_standard_conviction(self, notifier):
        entry = _make_entry(
            conviction="standard",
            confidence_score=0.70,
            suggested_risk_pct=0.01,
        )
        field = notifier._signal_field(entry)

        assert "STANDARD" in field["name"]
        assert "Risk: 1%" in field["value"]

    def test_embed_has_timestamp(self, notifier):
        embed = notifier._build_embed([], open_count=0, report_date="2026-03-20")
        assert "timestamp" in embed


# ---------------------------------------------------------------------------
# Webhook posting
# ---------------------------------------------------------------------------

class TestPost:
    """Tests for the post() method and webhook interaction."""

    def test_post_success(self, notifier):
        entries = [_make_entry()]

        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            result = notifier.post(entries, open_count=5, report_date="2026-03-20")

        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["color"] == COLOR_GREEN

    def test_post_zero_signals_still_sends(self, notifier):
        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            result = notifier.post([], open_count=0, report_date="2026-03-20")

        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert payload["embeds"][0]["color"] == COLOR_GREY

    def test_post_http_error_returns_false(self, notifier):
        with patch("requests.post", return_value=_mock_response(429, "Rate limited")):
            result = notifier.post([_make_entry()], open_count=1, report_date="2026-03-20")

        assert result is False

    def test_post_network_error_returns_false(self, notifier):
        with patch("requests.post", side_effect=requests.ConnectionError("fail")):
            result = notifier.post([_make_entry()], open_count=1, report_date="2026-03-20")

        assert result is False

    def test_no_url_returns_false(self, notifier_no_url):
        result = notifier_no_url.post([_make_entry()], open_count=1)
        assert result is False

    def test_default_report_date_uses_utc_today(self, notifier):
        with patch("requests.post", return_value=_mock_response(204)):
            result = notifier.post([_make_entry()], open_count=0)

        assert result is True

    def test_post_sends_to_correct_url(self, notifier):
        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            notifier.post([], open_count=0, report_date="2026-03-20")

        assert mock_post.call_args.args[0] == "https://discord.com/api/webhooks/test/token"


# ---------------------------------------------------------------------------
# Outcome notifications
# ---------------------------------------------------------------------------

class TestPostOutcome:
    """Tests for post_outcome() — signal resolution embeds."""

    def test_hit_tp_embed(self, notifier):
        signal = {
            "pair": "BTCUSDT", "status": "HIT_TP", "entry_high": 62000.0,
            "outcome_price": 66000.0, "timeframe": "1d",
            "stop_loss": 60000.0, "take_profit": 66000.0,
        }

        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            result = notifier.post_outcome(signal)

        assert result is True
        embed = mock_post.call_args.kwargs["json"]["embeds"][0]
        assert embed["color"] == COLOR_GREEN
        assert "TARGET HIT" in embed["title"]
        assert "BTCUSDT" in embed["title"]
        assert "+6.5%" in embed["description"]

    def test_hit_sl_embed(self, notifier):
        signal = {
            "pair": "ETHUSDT", "status": "HIT_SL", "entry_high": 3000.0,
            "outcome_price": 2850.0, "timeframe": "1d",
            "stop_loss": 2850.0, "take_profit": 3300.0,
        }

        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            result = notifier.post_outcome(signal)

        assert result is True
        embed = mock_post.call_args.kwargs["json"]["embeds"][0]
        assert embed["color"] == COLOR_RED
        assert "STOPPED OUT" in embed["title"]
        assert "-5.0%" in embed["description"]

    def test_expired_embed(self, notifier):
        signal = {
            "pair": "SOLUSDT", "status": "EXPIRED", "entry_high": 150.0,
            "timeframe": "1d", "stop_loss": 140.0, "take_profit": 170.0,
        }

        with patch("requests.post", return_value=_mock_response(204)) as mock_post:
            result = notifier.post_outcome(signal)

        assert result is True
        embed = mock_post.call_args.kwargs["json"]["embeds"][0]
        assert embed["color"] == COLOR_GOLD
        assert "EXPIRED" in embed["title"]
        assert "14 days" in embed["description"]

    def test_unknown_status_returns_false(self, notifier):
        signal = {"pair": "BTCUSDT", "status": "OPEN", "timeframe": "1d"}
        result = notifier.post_outcome(signal)
        assert result is False

    def test_no_url_returns_false(self, notifier_no_url):
        signal = {
            "pair": "BTCUSDT", "status": "HIT_TP", "entry_high": 62000.0,
            "outcome_price": 66000.0, "timeframe": "1d",
        }
        result = notifier_no_url.post_outcome(signal)
        assert result is False
