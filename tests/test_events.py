"""Tests for the pipeline event bus."""

from __future__ import annotations

from tenth_floor.events import EventBus, EventType, PipelineEvent


class TestEventBus:
    def test_emit_stores_event(self):
        bus = EventBus()
        run_id = bus.new_run_id()

        event = bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 36})

        assert isinstance(event, PipelineEvent)
        assert event.run_id == run_id
        assert event.type == "pipeline_started"
        assert event.payload == {"total_assets": 36}
        assert event.timestamp  # ISO8601 string

    def test_events_for_returns_all_in_order(self):
        bus = EventBus()
        run_id = bus.new_run_id()

        bus.emit(run_id, EventType.PIPELINE_STARTED, {"n": 1})
        bus.emit(run_id, EventType.MACRO_COMPLETE, {"n": 2})
        bus.emit(run_id, EventType.PIPELINE_COMPLETE, {"n": 3})

        events = bus.events_for(run_id)
        assert len(events) == 3
        assert [e.payload["n"] for e in events] == [1, 2, 3]
        assert [e.type for e in events] == [
            "pipeline_started", "macro_complete", "pipeline_complete",
        ]

    def test_events_for_unknown_run_id_returns_empty(self):
        bus = EventBus()
        assert bus.events_for("nonexistent") == []

    def test_events_isolated_by_run_id(self):
        bus = EventBus()
        run_a = "run_a"
        run_b = "run_b"

        bus.emit(run_a, EventType.PIPELINE_STARTED, {"who": "a"})
        bus.emit(run_b, EventType.PIPELINE_STARTED, {"who": "b"})

        assert len(bus.events_for(run_a)) == 1
        assert len(bus.events_for(run_b)) == 1
        assert bus.events_for(run_a)[0].payload["who"] == "a"
        assert bus.events_for(run_b)[0].payload["who"] == "b"

    def test_subscribe_fires_on_emit(self):
        bus = EventBus()
        received: list[PipelineEvent] = []

        bus.subscribe(received.append)
        bus.emit("run_1", EventType.ASSET_ANALYZED, {"symbol": "BTCUSDT"})

        assert len(received) == 1
        assert received[0].payload["symbol"] == "BTCUSDT"

    def test_unsubscribe_stops_firing(self):
        bus = EventBus()
        received: list[PipelineEvent] = []

        unsub = bus.subscribe(received.append)
        bus.emit("run_1", EventType.PIPELINE_STARTED, {})
        unsub()
        bus.emit("run_1", EventType.PIPELINE_COMPLETE, {})

        assert len(received) == 1

    def test_multiple_subscribers_all_fire(self):
        bus = EventBus()
        a: list[PipelineEvent] = []
        b: list[PipelineEvent] = []

        bus.subscribe(a.append)
        bus.subscribe(b.append)
        bus.emit("run_1", EventType.PIPELINE_STARTED, {})

        assert len(a) == 1
        assert len(b) == 1

    def test_subscriber_exception_does_not_break_bus(self):
        bus = EventBus()

        def broken(_event):
            raise RuntimeError("boom")

        received: list[PipelineEvent] = []
        bus.subscribe(broken)
        bus.subscribe(received.append)

        # Should not raise — broken subscriber is caught
        bus.emit("run_1", EventType.PIPELINE_STARTED, {})

        # Second subscriber still ran
        assert len(received) == 1

    def test_clear_specific_run(self):
        bus = EventBus()
        bus.emit("run_a", EventType.PIPELINE_STARTED, {})
        bus.emit("run_b", EventType.PIPELINE_STARTED, {})

        bus.clear("run_a")

        assert bus.events_for("run_a") == []
        assert len(bus.events_for("run_b")) == 1

    def test_clear_all(self):
        bus = EventBus()
        bus.emit("run_a", EventType.PIPELINE_STARTED, {})
        bus.emit("run_b", EventType.PIPELINE_STARTED, {})

        bus.clear()

        assert bus.events_for("run_a") == []
        assert bus.events_for("run_b") == []

    def test_event_to_dict_is_json_shaped(self):
        bus = EventBus()
        event = bus.emit("run_1", EventType.MACRO_COMPLETE, {"regime": "risk_on"})

        d = event.to_dict()
        assert d == {
            "run_id": "run_1",
            "type": "macro_complete",
            "timestamp": event.timestamp,
            "payload": {"regime": "risk_on"},
        }

    def test_new_run_id_is_unique(self):
        bus = EventBus()
        ids = {bus.new_run_id() for _ in range(50)}
        assert len(ids) == 50

    def test_emit_accepts_string_type(self):
        """Custom event types (not in enum) should still work."""
        bus = EventBus()
        event = bus.emit("run_1", "custom_event_type", {})
        assert event.type == "custom_event_type"


class TestPipelineEventEmission:
    """Integration-level: verify run_pipeline emits the expected event sequence."""

    def test_pipeline_emits_expected_event_sequence(self):
        """End-to-end smoke test using the same mocks as test_main."""
        from unittest.mock import patch

        from tenth_floor.events import event_bus
        from tenth_floor.main import run_pipeline
        from tests.test_main import _build_patches  # reuse the pipeline mock harness

        event_bus.clear()
        patches, _ = _build_patches()
        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_id = event_bus.new_run_id()
            run_pipeline(dry_run=True, run_id=run_id)
        finally:
            for cm in ctx_managers:
                cm.stop()

        events = event_bus.events_for(run_id)
        event_types = [e.type for e in events]

        assert "pipeline_started" in event_types
        assert "macro_complete" in event_types
        assert "asset_analyzed" in event_types
        assert "validation_result" in event_types
        assert "reviewer_decision" in event_types
        assert "pipeline_complete" in event_types

        # pipeline_started must be first, pipeline_complete must be last
        assert event_types[0] == "pipeline_started"
        assert event_types[-1] == "pipeline_complete"

        # Complete event carries elapsed + funnel
        complete = events[-1]
        assert "elapsed_seconds" in complete.payload
        assert "funnel" in complete.payload
        assert complete.payload["aborted"] is False

    def test_pipeline_error_emits_error_event(self):
        """If pipeline crashes, a pipeline_error event should be emitted."""
        from unittest.mock import MagicMock, patch

        from tenth_floor.events import event_bus
        from tenth_floor.main import run_pipeline
        from tests.test_main import _PATCH_BASE, _build_patches

        event_bus.clear()
        patches, _ = _build_patches()
        # Force the snapshot builder to explode so the except path fires
        broken_builder = MagicMock()
        broken_builder.build_universe.side_effect = RuntimeError("boom")
        patches[f"{_PATCH_BASE}.SnapshotBuilder"] = MagicMock(return_value=broken_builder)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_id = event_bus.new_run_id()
            try:
                run_pipeline(dry_run=True, run_id=run_id)
            except RuntimeError:
                pass  # expected — the exception re-raises after the event is emitted
        finally:
            for cm in ctx_managers:
                cm.stop()

        events = event_bus.events_for(run_id)
        event_types = [e.type for e in events]

        assert "pipeline_started" in event_types
        assert "pipeline_error" in event_types
        error_event = next(e for e in events if e.type == "pipeline_error")
        assert error_event.payload["error_type"] == "RuntimeError"
        assert "boom" in error_event.payload["message"]
