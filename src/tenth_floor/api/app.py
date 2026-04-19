"""FastAPI app factory for the local dashboard backend.

Launch with::

    uvicorn tenth_floor.api.app:app --host 127.0.0.1 --port 8765

Port 8765 avoids a collision with vLLM, which defaults to 8000.

Bind is explicit — never expose this to the network. No auth, no TLS.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tenth_floor.api import llm, runs, signals, ws


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="The Tenth Floor — Dashboard API",
        description="Local-only backend for the personal research dashboard.",
        version="0.1.0",
    )

    # Vite dev server origin. React production build will be served by the
    # same FastAPI app as static files later, so no CORS needed in prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(runs.router)
    app.include_router(signals.router)
    app.include_router(llm.router)
    app.include_router(ws.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
