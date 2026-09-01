from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from soulsaka.db import captures as captures_db
from soulsaka.db import jobs as jobs_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.ml.audio import to_wav16k, wav_duration
from soulsaka.models import CaptureIn, CaptureOut
from soulsaka.util.time import parse_iso

router = APIRouter()

_EXT_BY_TYPE = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/mpeg": "mp3",
    "audio/flac": "flac",
}


def _enqueue(state, cap: CaptureOut) -> None:
    jobs_db.enqueue(state.db, "process_capture", {"uid": cap.uid}, priority=2)
    state.events.publish("capture", uid=cap.uid, status="pending", kind=cap.kind)


@router.post("/captures", response_model=CaptureOut)
def create_text_capture(cap: CaptureIn, request: Request, device=Depends(current_device)):
    if cap.kind != "text":
        raise HTTPException(status_code=400, detail="use /captures/audio for audio")
    if not (cap.text or "").strip():
        raise HTTPException(status_code=400, detail="empty text")
    state = get_state(request)
    out, created = captures_db.create_capture(state.db, device.uid, cap)
    if created:
        _enqueue(state, out)
    return JSONResponse(out.model_dump(), status_code=201 if created else 200)


@router.post("/captures/audio", response_model=CaptureOut)
def create_audio_capture(
    request: Request,
    file: UploadFile = File(...),
    uid: str = Form(...),
    client_ts: str = Form(...),
    origin: str = Form("manual"),
    meta: str | None = Form(None),
    device=Depends(current_device),
):
    state = get_state(request)
    existing = captures_db.get_capture(state.db, uid)
    if existing:
        return JSONResponse(existing.model_dump(), status_code=200)
    ext = Path(file.filename or "").suffix.lstrip(".").lower() or _EXT_BY_TYPE.get(
        file.content_type or "", "bin"
    )
    upload = state.audio_path_for(uid, f"upload.{ext}")
    with upload.open("wb") as fh:
        while chunk := file.file.read(1 << 20):
            fh.write(chunk)
    wav = state.audio_path_for(uid, "wav")
    if to_wav16k(upload, wav):
        upload.unlink(missing_ok=True)
        stored = wav
    else:
        stored = upload
    duration = wav_duration(stored)
    try:
        meta_dict = json.loads(meta) if meta else None
    except json.JSONDecodeError:
        meta_dict = {"raw_meta": meta}
    cap = CaptureIn(
        uid=uid, kind="audio", origin=origin, client_ts=parse_iso(client_ts), meta=meta_dict
    )  # type: ignore[arg-type]
    out, created = captures_db.create_capture(
        state.db, device.uid, cap, audio_path=state.rel_path(stored), duration_s=duration
    )
    if created:
        _enqueue(state, out)
    else:
        stored.unlink(missing_ok=True)
    return JSONResponse(out.model_dump(), status_code=201 if created else 200)


@router.get("/captures", response_model=list[CaptureOut])
def list_captures(
    request: Request,
    since: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=500),
    device=Depends(current_device),
):
    return captures_db.list_captures(get_state(request).db, since=since, status=status, limit=limit)


@router.get("/captures/{uid}", response_model=CaptureOut)
def get_capture(uid: str, request: Request, device=Depends(current_device)):
    cap = captures_db.get_capture(get_state(request).db, uid)
    if cap is None:
        raise HTTPException(status_code=404, detail="no such capture")
    return cap


@router.get("/captures/{uid}/audio")
def get_capture_audio(uid: str, request: Request, device=Depends(current_device)):
    state = get_state(request)
    row = captures_db.get_capture_row(state.db, uid)
    if row is None or not row.get("audio_path"):
        raise HTTPException(status_code=404, detail="no audio")
    path = state.abs_path(row["audio_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio file missing")
    return FileResponse(path)


@router.post("/captures/{uid}/retry")
def retry_capture(uid: str, request: Request, device=Depends(current_device)):
    state = get_state(request)
    cap = captures_db.get_capture(state.db, uid)
    if cap is None:
        raise HTTPException(status_code=404, detail="no such capture")
    captures_db.update_capture(state.db, uid, status="pending", error=None)
    _enqueue(state, cap)
    return {"ok": True}


@router.delete("/captures/{uid}")
def delete_capture(uid: str, request: Request, device=Depends(current_device)):
    state = get_state(request)
    audio = captures_db.delete_capture(state.db, uid)
    if audio:
        state.abs_path(audio).unlink(missing_ok=True)
    return {"ok": True}
