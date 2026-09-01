from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from soulsaka.db import captures as captures_db
from soulsaka.db import memories as memories_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.models import SyncOut
from soulsaka.util.time import now_iso

router = APIRouter()


@router.get("/sync", response_model=SyncOut)
def sync(
    request: Request, since: str | None = None, limit: int = 200, device=Depends(current_device)
):
    state = get_state(request)
    server_time = now_iso()
    return SyncOut(
        server_time=server_time,
        memories=memories_db.list_memories(
            state.db, since=since, limit=limit, include_archived=True
        ),
        captures=captures_db.list_captures(state.db, since=since, limit=limit),
    )


@router.get("/events")
async def events(request: Request, device=Depends(current_device)):
    """Server-sent events: memory/capture/corpus changes as they happen."""
    state = get_state(request)
    q = state.events.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.25)
                    idle += 0.25
                    if idle >= 15:
                        idle = 0.0
                        yield ": keepalive\n\n"
                    continue
                idle = 0.0
                yield ev.sse()
        finally:
            state.events.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
