"""FastAPI backend for the local dashboard.

Exposes the pipeline as a one-click operation:
- ``POST /runs`` spawns the pipeline + outcome check in a background thread
- ``WS /ws`` streams every event from the event bus to the browser
- ``GET /signals`` / ``GET /stats`` read from SQLite for the track record view

Bind to ``127.0.0.1`` only. No auth, no TLS, local use only.
"""

from tenth_floor.api.app import create_app

__all__ = ["create_app"]
