from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from soulsaka.db import jobs as jobs_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.train import registry
from soulsaka.train.dataset import preview as dataset_preview

router = APIRouter()


class TrainRequest(BaseModel):
    version: str | None = None
    dry_run: bool = False


@router.get("/training/runs")
def runs(request: Request, device=Depends(current_device)):
    return registry.list_runs(get_state(request).db)


@router.get("/training/runs/{version}")
def run_detail(version: str, request: Request, device=Depends(current_device)):
    run = registry.get_run(get_state(request).db, version)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    return run


@router.post("/training/runs")
def start_run(req: TrainRequest, request: Request, device=Depends(current_device)):
    state = get_state(request)
    from soulsaka.train.runner import plan_version

    if any(r["status"] == "running" for r in registry.list_runs(state.db)):
        raise HTTPException(status_code=409, detail="a training run is already in progress")
    try:
        run = plan_version(state.db, state.settings, req.version)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    jobs_db.enqueue(
        state.db,
        "train",
        {"version": run["version"], "dry_run": req.dry_run},
        priority=-5,
        max_attempts=1,
    )
    state.events.publish("training", version=run["version"], status="queued")
    return run


@router.get("/training/dataset/preview")
def preview(request: Request, n: int = Query(5, le=50), device=Depends(current_device)):
    state = get_state(request)
    return dataset_preview(state.db, state.settings, n=n)
