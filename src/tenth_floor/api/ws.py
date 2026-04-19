"""WebSocket endpoint — single global stream with replay-on-connect.

Design: one endpoint, ``/ws``. Every client that connects:

1. Subscribes to the event bus with a threadsafe callback that pushes into
   a per-connection ``asyncio.Queue``.
2. Replays the full event history of the most recent run (if any), so a
   client that connects mid-run sees the timeline from the beginning.
3. Loops, awaiting queue items and forwarding them to the browser as JSON.

The pipeline runs in a background thread and emits events synchronously
on that thread. The bus subscriber uses ``loop.call_soon_threadsafe`` to
hand the event off to the asyncio queue without ever blocking the pipeline.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tenth_floor.events import PipelineEvent, event_bus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def pipeline_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    def _on_event(event: PipelineEvent) -> None:
        # Runs on the pipeline thread — hop back to the event loop.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = event_bus.subscribe(_on_event)

    try:
        # Replay the latest run's history before streaming live events.
        # We capture the replay set BEFORE subscribing matters? No — we subscribed
        # first, so any live event that arrives during replay is queued. We drain
        # the replay, then loop on the queue, which naturally dedupes because
        # each event is only in one place: either history (replayed once) or
        # new in the queue. For a run that is still in flight, events already
        # in history at the moment of subscribe may also arrive via the queue
        # if they were emitted between events_for() and subscribe(). To avoid
        # double-sending, we snapshot the sent event ids.
        sent: set[tuple[str, str, str]] = set()

        latest_run_id = event_bus.latest_run_id
        if latest_run_id:
            for event in event_bus.events_for(latest_run_id):
                key = (event.run_id, event.type, event.timestamp)
                sent.add(key)
                await websocket.send_json(event.to_dict())

        while True:
            event = await queue.get()
            key = (event.run_id, event.type, event.timestamp)
            if key in sent:
                continue
            sent.add(key)
            await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket handler crashed")
    finally:
        unsubscribe()
