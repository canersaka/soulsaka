from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from soulsaka.db import jobs as jobs_db
from soulsaka.db import memories as memories_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.hub.services import retrieval
from soulsaka.models import MemoryIn, MemoryOut, MemoryUpdate
from soulsaka.util.time import to_iso

router = APIRouter()


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    request: Request,
    q: str | None = None,
    since: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=1000),
    device=Depends(current_device),
):
    state = get_state(request)
    if q:
        return retrieval.search_memories(state, q, k=limit)
    return memories_db.list_memories(
        state.db, since=since, limit=limit, include_archived=include_archived, kind=kind
    )


@router.post("/memories", response_model=MemoryOut, status_code=201)
def create_memory(mem: MemoryIn, request: Request, device=Depends(current_device)):
    state = get_state(request)
    if not mem.text.strip():
        raise HTTPException(status_code=400, detail="empty text")
    out, created = memories_db.create_memory(
        state.db,
        mem.text,
        kind=mem.kind,
        source_kind="manual",
        source_ref=f"device:{device.uid}",
        uid=mem.uid,
        expires_at=to_iso(mem.expires_at) if mem.expires_at else None,
        meta=mem.meta,
    )
    if created:
        jobs_db.enqueue(state.db, "embed_memory", {"uid": out.uid}, priority=-2)
        state.events.publish("memory", uid=out.uid, kind=out.kind, text=out.text)
    return out


@router.get("/memories/{uid}", response_model=MemoryOut)
def get_memory(uid: str, request: Request, device=Depends(current_device)):
    out = memories_db.get_memory(get_state(request).db, uid)
    if out is None:
        raise HTTPException(status_code=404, detail="no such memory")
    return out


@router.patch("/memories/{uid}", response_model=MemoryOut)
def update_memory(uid: str, upd: MemoryUpdate, request: Request, device=Depends(current_device)):
    state = get_state(request)
    out = memories_db.update_memory(
        state.db,
        uid,
        text=upd.text,
        kind=upd.kind,
        archived=upd.archived,
        expires_at=to_iso(upd.expires_at) if upd.expires_at else None,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="no such memory")
    if upd.text:
        jobs_db.enqueue(state.db, "embed_memory", {"uid": uid}, priority=-2)
    state.events.publish("memory", uid=out.uid, kind=out.kind, text=out.text, archived=out.archived)
    return out


@router.delete("/memories/{uid}")
def delete_memory(uid: str, request: Request, device=Depends(current_device)):
    state = get_state(request)
    if not memories_db.delete_memory(state.db, uid):
        raise HTTPException(status_code=404, detail="no such memory")
    state.events.publish("memory", uid=uid, deleted=True)
    return {"ok": True}
