from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from soulsaka.db import jobs as jobs_db
from soulsaka.hub.auth import current_device, get_state

router = APIRouter()


@router.get("/jobs")
def jobs(request: Request, device=Depends(current_device)):
    db = get_state(request).db
    return {"counts": jobs_db.counts(db), "recent": jobs_db.recent(db, 30)}


@router.get("/speaker")
def speaker_status(request: Request, device=Depends(current_device)):
    state = get_state(request)
    try:
        return state.service("speaker").status(state.db)
    except Exception as e:  # noqa: BLE001 - backend not installed
        return {"enrolled": False, "ready": False, "error": str(e)}


@router.delete("/speaker")
def speaker_reset(request: Request, device=Depends(current_device)):
    state = get_state(request)
    state.service("speaker").reset_profile(state.db)
    return {"ok": True}


@router.post("/retrieval/backfill")
def backfill(request: Request, device=Depends(current_device)):
    from soulsaka.hub.services import retrieval

    return retrieval.backfill(get_state(request))


@router.get("/self-model")
def self_model(request: Request, device=Depends(current_device)):
    from soulsaka.hub.services.self_model import current, style_stats

    state = get_state(request)
    return {"markdown": current(state), "stats": style_stats(state.db)}


@router.post("/self-model/regenerate")
def self_model_regenerate(request: Request, device=Depends(current_device)):
    state = get_state(request)
    job = jobs_db.enqueue(state.db, "regen_self_model", {}, priority=-3, max_attempts=1)
    return {"job": job}
