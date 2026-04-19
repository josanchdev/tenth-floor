"""Pipeline run manager — one run at a time, pipeline then outcome check.

The dashboard calls ``POST /runs``. We spawn a background thread that:
  1. Runs the full pipeline (``run_pipeline``) — emits ``pipeline_*`` events.
  2. Runs the outcome checker (``check_outcomes``) — emits ``outcome_*`` events.
  3. Emits a final ``run_complete`` event so the frontend can flip back to idle.

One run_id covers both phases, so the WebSocket stream is a single coherent
timeline from the dashboard's point of view ("one click = one run").

Concurrency: a second ``start()`` while one run is in flight raises
``RunAlreadyActiveError``. The FastAPI layer translates that to ``409``.
"""

from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass
from typing import Any

from tenth_floor.check_outcomes import check_outcomes
from tenth_floor.events import EventType, event_bus
from tenth_floor.main import PipelineCancelled, run_pipeline

logger = logging.getLogger(__name__)


class RunAlreadyActiveError(RuntimeError):
    """Raised when a run is requested while another is still in flight."""


class NoActiveRunError(RuntimeError):
    """Raised when ``cancel()`` is called but no run is in flight."""


@dataclass
class RunRequest:
    """Parameters for a dashboard-initiated pipeline run."""

    symbols: list[str] | None = None
    asset_class: str | None = None
    dry_run: bool = False
    skip_outcome_check: bool = False  # escape hatch for tests


class RunManager:
    """Single-run lifecycle manager.

    Holds the currently active run_id (or None) under a lock, and exposes
    a ``start()`` method that spawns the background thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_run_id: str | None = None
        self._active_thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    def start(self, request: RunRequest) -> str:
        """Spawn a background run. Returns the new run_id.

        Raises ``RunAlreadyActiveError`` if a run is already in flight.
        """
        with self._lock:
            if self._active_run_id is not None:
                raise RunAlreadyActiveError(
                    f"Run {self._active_run_id} already active — wait for it to finish"
                )
            run_id = event_bus.new_run_id()
            self._active_run_id = run_id
            cancel_event = threading.Event()
            self._cancel_event = cancel_event
            thread = threading.Thread(
                target=self._run,
                args=(run_id, request, cancel_event),
                name=f"pipeline-{run_id}",
                daemon=True,
            )
            self._active_thread = thread

        thread.start()
        return run_id

    def cancel(self, run_id: str | None = None) -> str:
        """Request cancellation of the active run. Returns the cancelled run_id.

        Cooperative — the pipeline checks the event at phase boundaries and
        finishes its in-flight LLM call before unwinding. If ``run_id`` is
        provided, it must match the active run.
        """
        with self._lock:
            if self._active_run_id is None or self._cancel_event is None:
                raise NoActiveRunError("No active run to cancel")
            if run_id is not None and run_id != self._active_run_id:
                raise NoActiveRunError(
                    f"Run {run_id} is not active (active={self._active_run_id})"
                )
            active_id = self._active_run_id
            self._cancel_event.set()
        logger.info("Run %s: cancellation requested", active_id)
        return active_id

    def join(self, timeout: float | None = None) -> None:
        """Wait for the active run to finish — used in tests."""
        thread = self._active_thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(
        self,
        run_id: str,
        request: RunRequest,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread body — pipeline then outcome check."""
        try:
            logger.info("Run %s: starting pipeline", run_id)
            try:
                run_pipeline(
                    symbols=request.symbols,
                    dry_run=request.dry_run,
                    asset_class=request.asset_class,
                    run_id=run_id,
                    cancel_event=cancel_event,
                )
            except PipelineCancelled:
                logger.info("Run %s: pipeline cancelled", run_id)
                event_bus.emit(
                    run_id,
                    EventType.PIPELINE_CANCELLED,
                    {"phase": "pipeline"},
                )
                _emit_run_complete(run_id, ok=False, error="cancelled")
                return
            except Exception as exc:
                # run_pipeline already emits pipeline_error before re-raising.
                logger.exception("Run %s: pipeline crashed", run_id)
                _emit_run_complete(run_id, ok=False, error=str(exc))
                return

            if cancel_event.is_set():
                logger.info("Run %s: cancelled before outcome check", run_id)
                event_bus.emit(
                    run_id,
                    EventType.PIPELINE_CANCELLED,
                    {"phase": "pre_outcome_check"},
                )
                _emit_run_complete(run_id, ok=False, error="cancelled")
                return

            if request.skip_outcome_check:
                logger.info("Run %s: skip_outcome_check=True, finishing", run_id)
                _emit_run_complete(run_id, ok=True)
                return

            logger.info("Run %s: starting outcome check", run_id)
            try:
                check_outcomes(dry_run=request.dry_run, run_id=run_id)
            except Exception as exc:
                logger.exception("Run %s: outcome check crashed", run_id)
                event_bus.emit(
                    run_id,
                    EventType.PIPELINE_ERROR,
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "phase": "outcome_check",
                        "traceback": traceback.format_exc(limit=5),
                    },
                )
                _emit_run_complete(run_id, ok=False, error=str(exc))
                return

            _emit_run_complete(run_id, ok=True)
        finally:
            with self._lock:
                if self._active_run_id == run_id:
                    self._active_run_id = None
                    self._cancel_event = None


def _emit_run_complete(run_id: str, *, ok: bool, error: str | None = None) -> None:
    """Fire the final ``run_complete`` sentinel event."""
    payload: dict[str, Any] = {"ok": ok}
    if error is not None:
        payload["error"] = error
    event_bus.emit(run_id, EventType.RUN_COMPLETE, payload)


# Module-level singleton — the API router imports this directly.
run_manager = RunManager()
