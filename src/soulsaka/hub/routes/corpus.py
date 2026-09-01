from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from soulsaka.db import corpus as corpus_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.models import ImportReport, MessageBatch, MessageOut, SourceOut, StatsOut

router = APIRouter()


@router.get("/stats", response_model=StatsOut)
def stats(request: Request, device=Depends(current_device)):
    return corpus_db.stats(get_state(request).db)


@router.get("/sources", response_model=list[SourceOut])
def sources(request: Request, device=Depends(current_device)):
    return corpus_db.list_sources(get_state(request).db)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request, device=Depends(current_device)):
    if not corpus_db.delete_source(get_state(request).db, source_id):
        raise HTTPException(status_code=404, detail="no such source")
    return {"ok": True}


@router.post("/messages/batch", response_model=ImportReport)
def push_messages(batch: MessageBatch, request: Request, device=Depends(current_device)):
    state = get_state(request)
    report = corpus_db.ingest_messages(
        state.db,
        state.salt,
        batch.source,
        batch.messages,
        device_uid=device.uid,
        keep_names=state.settings.privacy.keep_contact_names,
    )
    if report.inserted:
        state.events.publish("corpus", source=batch.source.kind, inserted=report.inserted)
    return report


@router.get("/messages/search", response_model=list[MessageOut])
def search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=200),
    me_only: bool = True,
    device=Depends(current_device),
):
    return corpus_db.search_messages(get_state(request).db, q, limit=limit, me_only=me_only)
