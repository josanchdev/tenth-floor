"""Tests for the /ws WebSocket endpoint.

Verifies replay-on-connect and live event streaming.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tenth_floor.api.app import create_app
from tenth_floor.events import EventType, event_bus


@pytest.fixture
def client():
    event_bus.clear()
    return TestClient(create_app())


class TestWebSocket:
    def test_replay_latest_run_on_connect(self, client):
        """A client that connects after events were emitted sees all of them."""
        run_id = event_bus.new_run_id()
        event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 3})
        event_bus.emit(run_id, EventType.MACRO_COMPLETE, {"regime": "risk_on"})
        event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})

        with client.websocket_connect("/ws") as ws:
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()
            msg3 = ws.receive_json()

        assert msg1["type"] == "pipeline_started"
        assert msg1["run_id"] == run_id
        assert msg2["type"] == "macro_complete"
        assert msg3["type"] == "pipeline_complete"

    def test_live_events_after_connect(self, client):
        """Events emitted after the WS is open should be streamed immediately."""
        run_id = event_bus.new_run_id()
        event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 1})

        with client.websocket_connect("/ws") as ws:
            # Drain the replay
            ws.receive_json()

            # Emit a new event — should arrive on the same connection
            event_bus.emit(run_id, EventType.ASSET_ANALYZED, {"symbol": "BTCUSDT"})
            msg = ws.receive_json()

        assert msg["type"] == "asset_analyzed"
        assert msg["payload"]["symbol"] == "BTCUSDT"

    def test_connect_with_no_run_history(self, client):
        """Connecting before any run exists should succeed and stream a live event."""
        with client.websocket_connect("/ws") as ws:
            run_id = event_bus.new_run_id()
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 0})
            msg = ws.receive_json()

        assert msg["type"] == "pipeline_started"

    def test_dedup_no_double_send_during_replay_race(self, client):
        """Events in replay history should not also arrive live on the same connection."""
        run_id = event_bus.new_run_id()
        event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 3})

        with client.websocket_connect("/ws") as ws:
            # Replay delivers pipeline_started exactly once
            msg = ws.receive_json()
            assert msg["type"] == "pipeline_started"

            # New event arrives live
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})
            msg = ws.receive_json()
            assert msg["type"] == "pipeline_complete"

    def test_latest_run_id_follows_most_recent_pipeline_started(self, client):
        """Replay should target the newest run, not the first one recorded."""
        old_run = "old_run"
        event_bus.emit(old_run, EventType.PIPELINE_STARTED, {"n": 1})
        event_bus.emit(old_run, EventType.PIPELINE_COMPLETE, {"n": 1})

        new_run = "new_run"
        event_bus.emit(new_run, EventType.PIPELINE_STARTED, {"n": 2})
        event_bus.emit(new_run, EventType.PIPELINE_COMPLETE, {"n": 2})

        with client.websocket_connect("/ws") as ws:
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()

        assert msg1["run_id"] == new_run
        assert msg2["run_id"] == new_run


class TestHealthEndpoint:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
