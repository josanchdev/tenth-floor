"""
Pipeline event bus — in-memory pub/sub for real-time dashboard feedback.

The pipeline emits structured events at each stage (started, macro complete,
per-asset analysis, validation, review, complete). The EventBus stores them
per run_id and fans them out to subscribers. In Step 4 the FastAPI backend
will subscribe with a callback that broadcasts to WebSocket clients.

Design choices:
- Single-process, in-memory. No Redis, no asyncio.Queue — keep it simple.
  One dashboard user, one pipeline at a time.
- Events are stored per run_id so a late subscriber can replay the history
  of an in-progress run.
- Subscriber callbacks run inline with emit(). Exceptions in a subscriber
  are caught and logged so one broken subscriber cannot crash the pipeline.

Usage::

    from tenth_floor.events import event_bus, EventType

    run_id = event_bus.new_run_id()
    event_bus.emit(run_id, EventType.PIPELINE_STARTED, {"total_assets": 36})
    ...
    for event in event_bus.events_for(run_id):
        print(event.type, event.payload)
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    """All event types the pipeline can emit."""

    PIPELINE_STARTED = "pipeline_started"
    MACRO_COMPLETE = "macro_complete"
    ASSET_ANALYZED = "asset_analyzed"
    VALIDATION_RESULT = "validation_result"
    REVIEWER_DECISION = "reviewer_decision"
    PIPELINE_COMPLETE = "pipeline_complete"
    PIPELINE_ERROR = "pipeline_error"
    PIPELINE_CANCELLED = "pipeline_cancelled"
    OUTCOME_CHECK_STARTED = "outcome_check_started"
    OUTCOME_RESOLVED = "outcome_resolved"
    OUTCOME_CHECK_COMPLETE = "outcome_check_complete"
    RUN_COMPLETE = "run_complete"
    # LLM lifecycle — emitted by llm_manager, consumed by dashboard status pill.
    LLM_STARTING = "llm_starting"
    LLM_READY = "llm_ready"
    LLM_STOPPED = "llm_stopped"
    LLM_FAILED = "llm_failed"


@dataclass(frozen=True)
class PipelineEvent:
    """A single structured event emitted by the pipeline."""

    run_id: str
    type: str
    timestamp: str  # ISO8601 UTC
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation — ready for WebSocket broadcast."""
        return {
            "run_id": self.run_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


Subscriber = Callable[[PipelineEvent], None]


class EventBus:
    """In-memory pub/sub for pipeline events, keyed by run_id."""

    def __init__(self) -> None:
        self._events: dict[str, list[PipelineEvent]] = {}
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._latest_run_id: str | None = None

    @property
    def latest_run_id(self) -> str | None:
        """Most recently started run_id, or None if nothing has run yet.

        Set whenever a ``PIPELINE_STARTED`` event is recorded, so a
        WebSocket subscriber that connects mid-run (or between runs)
        knows which history to replay on connect.
        """
        with self._lock:
            return self._latest_run_id

    @staticmethod
    def new_run_id() -> str:
        """Generate a new run_id — date prefix + short uuid for human readability."""
        today = datetime.now(UTC).strftime("%Y%m%d")
        return f"{today}_{uuid.uuid4().hex[:8]}"

    def emit(
        self,
        run_id: str,
        type: EventType | str,
        payload: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        """Record an event and fan it out to all subscribers."""
        event = PipelineEvent(
            run_id=run_id,
            type=type.value if isinstance(type, EventType) else type,
            timestamp=datetime.now(UTC).isoformat(),
            payload=payload or {},
        )
        with self._lock:
            self._events.setdefault(run_id, []).append(event)
            if event.type == EventType.PIPELINE_STARTED.value:
                self._latest_run_id = run_id
            subs = list(self._subscribers)

        for sub in subs:
            try:
                sub(event)
            except Exception:
                logger.exception("Event subscriber raised — continuing")

        return event

    def events_for(self, run_id: str) -> list[PipelineEvent]:
        """Return all events recorded for a run, in order."""
        with self._lock:
            return list(self._events.get(run_id, []))

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a callback. Returns an unsubscribe function."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def clear(self, run_id: str | None = None) -> None:
        """Clear recorded events. Pass run_id to clear one run, else clear all."""
        with self._lock:
            if run_id is None:
                self._events.clear()
                self._latest_run_id = None
            else:
                self._events.pop(run_id, None)
                if self._latest_run_id == run_id:
                    self._latest_run_id = None


# Module-level singleton — the pipeline writes to this, the dashboard reads.
event_bus = EventBus()
