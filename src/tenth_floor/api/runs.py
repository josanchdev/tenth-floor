"""Run-control endpoints — spawn pipeline runs and query their state."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tenth_floor.api.runner import (
    NoActiveRunError,
    RunAlreadyActiveError,
    RunRequest,
    run_manager,
)
from tenth_floor.events import event_bus

router = APIRouter(tags=["runs"])


class StartRunBody(BaseModel):
    """Request body for ``POST /runs``."""

    symbols: list[str] | None = Field(
        default=None,
        description="Optional whitelist of symbols. None = full universe.",
    )
    asset_class: str | None = Field(
        default=None,
        description="Filter to one asset class (crypto, equity, etf, commodity).",
    )
    dry_run: bool = Field(
        default=False,
        description="Skip SQLite writes + outcome persistence.",
    )


class StartRunResponse(BaseModel):
    run_id: str


class CancelRunResponse(BaseModel):
    run_id: str
    cancelled: bool = True


class ActiveRunResponse(BaseModel):
    run_id: str | None


class RunEventsResponse(BaseModel):
    run_id: str
    events: list[dict]


@router.post("/runs", response_model=StartRunResponse, status_code=202)
def start_run(body: StartRunBody) -> StartRunResponse:
    """Spawn a new pipeline + outcome-check run in a background thread.

    Returns ``202 Accepted`` with the new run_id. Events stream over ``/ws``.
    Returns ``409 Conflict`` if a run is already active.
    """
    try:
        run_id = run_manager.start(
            RunRequest(
                symbols=body.symbols,
                asset_class=body.asset_class,
                dry_run=body.dry_run,
            )
        )
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartRunResponse(run_id=run_id)


@router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
def cancel_run(run_id: str) -> CancelRunResponse:
    """Cooperatively cancel the active run.

    Returns ``404`` if no run is active or the id doesn't match. The
    pipeline finishes its in-flight LLM call and emits ``pipeline_cancelled``
    before the background thread unwinds.
    """
    try:
        cancelled_id = run_manager.cancel(run_id)
    except NoActiveRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CancelRunResponse(run_id=cancelled_id)


@router.get("/runs/active", response_model=ActiveRunResponse)
def get_active_run() -> ActiveRunResponse:
    """Return the currently active run_id, or null if idle.

    The dashboard calls this on cold open to decide whether to show the
    live pipeline pane or the idle "Run now" pane.
    """
    return ActiveRunResponse(run_id=run_manager.active_run_id)


@router.get("/runs/latest", response_model=RunEventsResponse)
def get_latest_run_events() -> RunEventsResponse:
    """Events from the most recent run (active or finished).

    Used by the SKIPPED tier on Track Record — SKIP decisions are
    ephemeral (event-bus-only, never persisted), so the dashboard
    reads them from the latest run's events.
    """
    run_id = event_bus.latest_run_id
    if run_id is None:
        return RunEventsResponse(run_id="", events=[])
    events = event_bus.events_for(run_id)
    return RunEventsResponse(run_id=run_id, events=[e.to_dict() for e in events])


@router.get("/runs/{run_id}", response_model=RunEventsResponse)
def get_run_events(run_id: str) -> RunEventsResponse:
    """Full event history for a run — HTTP alternative to the WS replay."""
    events = event_bus.events_for(run_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"No events for run {run_id}")
    return RunEventsResponse(run_id=run_id, events=[e.to_dict() for e in events])
