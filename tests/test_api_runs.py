"""Tests for POST /runs, GET /runs/active, GET /runs/{id}."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tenth_floor.api.app import create_app
from tenth_floor.api.runner import RunManager, RunRequest
from tenth_floor.events import EventType, event_bus


@pytest.fixture
def client():
    event_bus.clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_manager():
    """Ensure the module-level run_manager is idle before every test."""
    from tenth_floor.api import runner

    runner.run_manager = RunManager()
    # Rebind the imported reference inside runs.py
    from tenth_floor.api import runs as runs_module

    runs_module.run_manager = runner.run_manager
    yield
    event_bus.clear()


class TestRunsEndpoint:
    def test_start_run_returns_run_id(self, client):
        """POST /runs with a fake pipeline returns 202 + a new run_id."""
        def fake_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 0})
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})
            return []

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=fake_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", return_value={}):
            response = client.post("/runs", json={"dry_run": True})

        assert response.status_code == 202
        data = response.json()
        assert "run_id" in data
        assert data["run_id"].startswith("2")  # date-prefixed

    def test_second_run_while_active_returns_409(self, client):
        """A slow first run blocks a second POST /runs until it finishes."""
        first_started = threading.Event()
        release_first = threading.Event()

        def slow_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 0})
            first_started.set()
            release_first.wait(timeout=5)
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=slow_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", return_value={}):
            response_a = client.post("/runs", json={"dry_run": True})
            assert response_a.status_code == 202
            first_started.wait(timeout=5)

            response_b = client.post("/runs", json={"dry_run": True})
            assert response_b.status_code == 409
            assert "already active" in response_b.json()["detail"]

            release_first.set()
            # Let the first run finish so the fixture cleanup is clean.
            from tenth_floor.api.runner import run_manager as rm_after_patch  # noqa: F401
            from tenth_floor.api.runs import run_manager

            run_manager.join(timeout=5)

    def test_active_run_endpoint_returns_null_when_idle(self, client):
        response = client.get("/runs/active")
        assert response.status_code == 200
        assert response.json() == {"run_id": None}

    def test_get_run_events_returns_history(self, client):
        """GET /runs/{id} returns all recorded events for that run."""
        run_id = event_bus.new_run_id()
        event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 5})
        event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})

        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert len(body["events"]) == 2
        assert body["events"][0]["type"] == "pipeline_started"

    def test_get_run_events_unknown_run_id_returns_404(self, client):
        response = client.get("/runs/does_not_exist")
        assert response.status_code == 404


class TestRunManagerOutcomePhase:
    """Verify the run manager chains outcome_check automatically after the pipeline."""

    def test_outcome_check_runs_after_pipeline(self):
        from tenth_floor.api.runner import RunManager

        manager = RunManager()
        pipeline_calls: list[str] = []
        outcome_calls: list[str] = []

        def fake_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            pipeline_calls.append(run_id)
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {})
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"published_count": 0})

        def fake_outcomes(dry_run=False, run_id=None):
            outcome_calls.append(run_id)
            event_bus.emit(run_id, EventType.OUTCOME_CHECK_STARTED, {"active_count": 0})
            event_bus.emit(run_id, EventType.OUTCOME_CHECK_COMPLETE, {"summary": {}})
            return {}

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=fake_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", side_effect=fake_outcomes):
            run_id = manager.start(RunRequest(dry_run=True))
            manager.join(timeout=5)

        assert pipeline_calls == [run_id]
        assert outcome_calls == [run_id]
        types = [e.type for e in event_bus.events_for(run_id)]
        assert "pipeline_started" in types
        assert "pipeline_complete" in types
        assert "outcome_check_started" in types
        assert "outcome_check_complete" in types
        assert types[-1] == "run_complete"
        assert manager.active_run_id is None

    def test_pipeline_crash_still_emits_run_complete_and_skips_outcomes(self):
        from tenth_floor.api.runner import RunManager

        manager = RunManager()
        outcome_called = False

        def crashing_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {})
            raise RuntimeError("boom")

        def fake_outcomes(dry_run=False, run_id=None):
            nonlocal outcome_called
            outcome_called = True
            return {}

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=crashing_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", side_effect=fake_outcomes):
            run_id = manager.start(RunRequest(dry_run=True))
            manager.join(timeout=5)

        assert outcome_called is False
        types = [e.type for e in event_bus.events_for(run_id)]
        assert types[-1] == "run_complete"
        complete = event_bus.events_for(run_id)[-1]
        assert complete.payload["ok"] is False
        assert manager.active_run_id is None

    def test_skip_outcome_check_flag(self):
        from tenth_floor.api.runner import RunManager

        manager = RunManager()
        outcome_called = False

        def fake_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {})
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {})

        def fake_outcomes(dry_run=False, run_id=None):
            nonlocal outcome_called
            outcome_called = True
            return {}

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=fake_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", side_effect=fake_outcomes):
            run_id = manager.start(RunRequest(dry_run=True, skip_outcome_check=True))
            manager.join(timeout=5)

        assert outcome_called is False
        types = [e.type for e in event_bus.events_for(run_id)]
        assert types[-1] == "run_complete"
        # Ensure we still marked ok=True
        assert event_bus.events_for(run_id)[-1].payload["ok"] is True

    def test_active_run_id_is_set_during_run(self):
        """Before start → None. During run → run_id. After join → None."""
        from tenth_floor.api.runner import RunManager

        manager = RunManager()
        assert manager.active_run_id is None

        gate = threading.Event()

        def gated_pipeline(symbols=None, dry_run=False, asset_class=None, run_id=None, cancel_event=None):
            event_bus.emit(run_id, EventType.PIPELINE_STARTED, {})
            gate.wait(timeout=5)
            event_bus.emit(run_id, EventType.PIPELINE_COMPLETE, {})

        with patch("tenth_floor.api.runner.run_pipeline", side_effect=gated_pipeline), \
             patch("tenth_floor.api.runner.check_outcomes", return_value={}):
            run_id = manager.start(RunRequest(dry_run=True))
            # Give the thread a moment to enter run_pipeline
            time.sleep(0.05)
            assert manager.active_run_id == run_id
            gate.set()
            manager.join(timeout=5)

        assert manager.active_run_id is None
