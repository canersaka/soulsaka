from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request

from soulsaka.db import devices as devices_db
from soulsaka.db.devices import LOCAL_DEVICE, Device
from soulsaka.hub.state import HubState

CLIENT_HEADER = "x-soulsaka-client"


def get_state(request: Request) -> HubState:
    return request.app.state.hub


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def current_device(request: Request) -> Device:
    """Resolve the calling device from a bearer token.

    Requests from the hub machine itself (loopback) that carry the ``X-Soulsaka-Client``
    header are trusted without a token when ``hub.trust_loopback`` is on. The header
    requirement forces a CORS preflight, so a web page you happen to visit cannot poke
    the hub through your browser.
    """
    state = get_state(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        device = devices_db.device_by_token(state.db, token)
        if device is None:
            raise HTTPException(status_code=401, detail="invalid device token")
        devices_db.touch_device(state.db, device.uid)
        return device
    client_host = request.client.host if request.client else None
    if (
        state.settings.hub.trust_loopback
        and _is_loopback(client_host)
        and request.headers.get(CLIENT_HEADER)
    ):
        return LOCAL_DEVICE
    raise HTTPException(status_code=401, detail="pair this device first")
