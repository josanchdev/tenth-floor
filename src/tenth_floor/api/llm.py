"""LLM lifecycle endpoints — start/stop/status for the local vLLM server.

Thin HTTP wrapper over ``llm_manager``. The dashboard pill polls
``GET /llm/status`` every few seconds and merges WebSocket lifecycle
events for instant transitions.
"""

from __future__ import annotations

from fastapi import APIRouter

from tenth_floor.api.llm_manager import llm_manager

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/status")
def get_status() -> dict:
    """Current vLLM state — probes /v1/models every call."""
    return llm_manager.status()


@router.post("/start")
def start() -> dict:
    """Spawn vLLM if not already running. Idempotent."""
    return llm_manager.start()


@router.post("/stop")
def stop() -> dict:
    """SIGTERM the tracked vLLM subprocess."""
    return llm_manager.stop()
