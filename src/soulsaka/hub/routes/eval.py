from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from soulsaka.db import jobs as jobs_db
from soulsaka.eval import pairs as pairs_svc
from soulsaka.eval import report
from soulsaka.hub.auth import current_device, get_state

router = APIRouter()


class GenerateRequest(BaseModel):
    version: str
    n: int = 20
    profile: str | None = None


class VersionRequest(BaseModel):
    version: str


class GuessRequest(BaseModel):
    rater: str
    guessed_first: bool


@router.get("/eval/summary")
def summary(request: Request, device=Depends(current_device)):
    return report.summary(get_state(request).db)


@router.get("/eval/summary.svg")
def summary_svg(request: Request, device=Depends(current_device)):
    from fastapi.responses import Response

    data = report.summary(get_state(request).db)
    return Response(report.render_svg(data), media_type="image/svg+xml")


@router.post("/eval/generate")
def generate(req: GenerateRequest, request: Request, device=Depends(current_device)):
    state = get_state(request)
    job = jobs_db.enqueue(state.db, "eval_generate", req.model_dump(), priority=-3, max_attempts=1)
    return {"job": job, "version": req.version}


@router.post("/eval/discriminator")
def discriminator(req: VersionRequest, request: Request, device=Depends(current_device)):
    state = get_state(request)
    job = jobs_db.enqueue(
        state.db, "eval_discriminator", req.model_dump(), priority=-3, max_attempts=1
    )
    return {"job": job, "version": req.version}


@router.post("/eval/voice")
def voice(req: VersionRequest, request: Request, device=Depends(current_device)):
    state = get_state(request)
    job = jobs_db.enqueue(state.db, "eval_voice", req.model_dump(), priority=-3, max_attempts=1)
    return {"job": job, "version": req.version}


@router.get("/eval/blind/{version}")
def blind(version: str, request: Request, device=Depends(current_device)):
    return pairs_svc.blind_summary(get_state(request).db, version)


# --- rating endpoints: no device token, so friends on the LAN can rate --------------


@router.get("/eval/pairs")
def pairs(
    request: Request,
    version: str = Query(...),
    rater: str = Query(..., min_length=1),
    limit: int = Query(20, le=100),
):
    return pairs_svc.pairs_for_rater(get_state(request).db, version, rater.strip(), limit)


@router.post("/eval/pairs/{uid}/guess")
def guess(uid: str, req: GuessRequest, request: Request):
    if not req.rater.strip():
        raise HTTPException(status_code=400, detail="rater name required")
    correct = pairs_svc.record_guess(get_state(request).db, uid, req.rater, req.guessed_first)
    if correct is None:
        raise HTTPException(status_code=404, detail="no such pair")
    return {"correct": correct}


@router.get("/eval/pairs/{version}/score")
def score(version: str, request: Request, rater: str = Query(..., min_length=1)):
    return pairs_svc.rater_score(get_state(request).db, version, rater.strip())
