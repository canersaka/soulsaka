from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from soulsaka.hub.auth import current_device, get_state
from soulsaka.paths import evals_dir
from soulsaka.voice import reference

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str


@router.get("/voice/reference")
def get_reference(request: Request, device=Depends(current_device)):
    state = get_state(request)
    clip, text = reference.get_reference(state.db)
    return {
        "reference_clip": clip,
        "reference_text": text,
        "candidates": len(reference.candidates(state.db, state.root)),
    }


@router.post("/voice/reference")
def build_reference(request: Request, device=Depends(current_device)):
    state = get_state(request)
    try:
        out = reference.build_reference(state.db, root=state.root)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    state._services.pop("tts", None)  # rebuild TTS with the new reference on next use
    state.events.publish("voice", reference_seconds=out["seconds"])
    return out


@router.get("/voice/reference/audio")
def reference_audio(request: Request, device=Depends(current_device)):
    state = get_state(request)
    clip, _ = reference.get_reference(state.db)
    if not clip or not state.abs_path(clip).exists():
        raise HTTPException(status_code=404, detail="no reference clip")
    return FileResponse(state.abs_path(clip), media_type="audio/wav")


@router.post("/voice/speak")
def speak(req: SpeakRequest, request: Request, device=Depends(current_device)):
    """Synthesize text in my voice; returns a WAV."""
    state = get_state(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    try:
        tts = state.service("tts")
    except Exception as e:  # noqa: BLE001 - backend not installed / no reference yet
        raise HTTPException(status_code=503, detail=str(e)) from e
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    out = evals_dir() / "tts-cache" / f"{digest}.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tts.synthesize(text, out)
    return FileResponse(out, media_type="audio/wav")
