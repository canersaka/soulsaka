from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from soulsaka import __version__
from soulsaka.db import devices as devices_db
from soulsaka.hub.auth import current_device, get_state
from soulsaka.models import DeviceOut, PairRequest, PairResponse

router = APIRouter()


@router.get("/health")
def health(request: Request):
    state = get_state(request)
    return {
        "ok": True,
        "version": __version__,
        "devices": len(devices_db.list_devices(state.db)),
        "name": state.settings.me.display_name or None,
    }


@router.post("/pair", response_model=PairResponse)
def pair(req: PairRequest, request: Request):
    state = get_state(request)
    result = devices_db.redeem_pairing_code(state.db, req.code, req.name, req.kind)
    if result is None:
        raise HTTPException(status_code=400, detail="invalid or expired pairing code")
    device, token = result
    state.events.publish("device", uid=device.uid, name=device.name, kind=device.kind)
    return PairResponse(device_uid=device.uid, token=token)


@router.get("/me", response_model=DeviceOut)
def me(device=Depends(current_device)):
    return DeviceOut(**device.__dict__)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(request: Request, device=Depends(current_device)):
    state = get_state(request)
    return [DeviceOut(**d.__dict__) for d in devices_db.list_devices(state.db)]


@router.post("/devices/pairing-code")
def new_pairing_code(request: Request, device=Depends(current_device)):
    """Mint a pairing code from an already-trusted device (e.g. the hub's own browser)."""
    state = get_state(request)
    code = devices_db.create_pairing_code(state.db)
    return {"code": code, "ttl_s": 600}


@router.delete("/devices/{uid}")
def revoke(uid: str, request: Request, device=Depends(current_device)):
    state = get_state(request)
    if not devices_db.revoke_device(state.db, uid):
        raise HTTPException(status_code=404, detail="no such device")
    return {"ok": True}


@router.get("/config")
def public_config(request: Request, device=Depends(current_device)):
    """Non-secret settings the UI needs."""
    s = get_state(request).settings
    llm = get_state(request).service("llm")
    return {
        "me": {"display_name": s.me.display_name, "names": s.me.names},
        "privacy": s.privacy.model_dump(),
        "llm": {"default": s.llm.default, "profiles": llm.list_profiles()},
        "speaker": {
            "threshold": s.speaker.threshold,
            "min_enroll_samples": s.speaker.min_enroll_samples,
        },
        "asr": {"backend": s.asr.backend, "model": s.asr.model},
        "train": {"base_model": s.train.base_model, "backend": s.train.backend},
    }
