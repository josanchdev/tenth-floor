"""Tests for GET /signals, /signals/{id}, /stats — SQLite-backed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tenth_floor.api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def _make_signal(**overrides):
    base = {
        "signal_id": "20260411_BTCUSDT_1d_abc",
        "pair": "BTCUSDT",
        "timeframe": "1d",
        "report_date": "2026-04-11",
        "asset_class": "crypto",
        "status": "PENDING",
        "direction": "long",
        "entry_low": 60000.0,
        "entry_high": 61000.0,
        "stop_loss": 58000.0,
        "take_profit": 66000.0,
        "reward_risk": 2.0,
        "confidence_score": 0.75,
        "conviction": "high",
        "rationale": "test",
        "created_at": "2026-04-11T10:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestListSignals:
    def test_list_all_signals(self, client):
        fake_signals = [
            _make_signal(signal_id="a", asset_class="crypto"),
            _make_signal(signal_id="b", asset_class="equity", pair="AAPL"),
        ]
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = fake_signals

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/signals")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        assert len(body["signals"]) == 2

    def test_filter_by_asset_class(self, client):
        fake_signals = [
            _make_signal(signal_id="a", asset_class="crypto"),
            _make_signal(signal_id="b", asset_class="equity", pair="AAPL"),
        ]
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = fake_signals

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/signals", params={"asset_class": "equity"})

        body = response.json()
        assert body["count"] == 1
        assert body["signals"][0]["signal_id"] == "b"

    def test_filter_by_status(self, client):
        fake_signals = [
            _make_signal(signal_id="a", status="HIT_TP"),
            _make_signal(signal_id="b", status="HIT_SL"),
            _make_signal(signal_id="c", status="PENDING"),
        ]
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = fake_signals

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/signals", params={"status": "HIT_TP"})

        body = response.json()
        assert body["count"] == 1
        assert body["signals"][0]["signal_id"] == "a"


class TestGetSignal:
    def test_existing_signal(self, client):
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_signal.return_value = _make_signal()

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/signals/20260411_BTCUSDT_1d_abc")

        assert response.status_code == 200
        assert response.json()["pair"] == "BTCUSDT"

    def test_missing_signal_returns_404(self, client):
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_signal.return_value = None

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/signals/unknown")

        assert response.status_code == 404


class TestStats:
    def test_empty_stats(self, client):
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = []
        sl.open_signal_count.return_value = 0

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/stats")

        body = response.json()
        assert body["n_resolved"] == 0
        assert body["expectancy_r"] is None
        assert body["expectancy_ci_low"] is None

    def test_expectancy_with_mixed_outcomes(self, client):
        # 3 wins at R:R 2.0, 2 losses at -1R → expectancy = (2+2+2-1-1)/5 = 0.8
        fake = [
            _make_signal(signal_id="1", status="HIT_TP", reward_risk=2.0),
            _make_signal(signal_id="2", status="HIT_TP", reward_risk=2.0),
            _make_signal(signal_id="3", status="HIT_TP", reward_risk=2.0),
            _make_signal(signal_id="4", status="HIT_SL", reward_risk=2.0),
            _make_signal(signal_id="5", status="HIT_SL", reward_risk=2.0),
        ]
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = fake
        sl.open_signal_count.return_value = 0

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/stats")

        body = response.json()
        assert body["n_resolved"] == 5
        assert body["wins"] == 3
        assert body["losses"] == 2
        assert body["expired"] == 0
        assert abs(body["expectancy_r"] - 0.8) < 1e-9
        assert body["win_rate"] == 0.6
        # CI should bracket the point estimate
        assert body["expectancy_ci_low"] < body["expectancy_r"] < body["expectancy_ci_high"]

    def test_expired_signals_count_as_zero_r(self, client):
        fake = [
            _make_signal(signal_id="1", status="EXPIRED", reward_risk=2.0),
            _make_signal(signal_id="2", status="EXPIRED", reward_risk=2.0),
        ]
        sl = MagicMock()
        sl.__enter__ = MagicMock(return_value=sl)
        sl.__exit__ = MagicMock(return_value=None)
        sl.get_recent_signals.return_value = fake
        sl.open_signal_count.return_value = 0

        with patch("tenth_floor.api.signals.SignalLogger", return_value=sl):
            response = client.get("/stats")

        body = response.json()
        assert body["expired"] == 2
        assert body["expectancy_r"] == 0.0
