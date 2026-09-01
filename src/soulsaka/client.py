"""HTTP client for the hub, used by importers, the listener and the CLI on other devices."""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from soulsaka.models import (
    CaptureOut,
    ImportReport,
    MemoryOut,
    MessageBatch,
    PairResponse,
    StatsOut,
    SyncOut,
)
from soulsaka.paths import data_dir
from soulsaka.util.ids import new_uid
from soulsaka.util.time import to_iso, utcnow

CLIENT_HEADERS = {"X-Soulsaka-Client": "python"}


@dataclass
class ClientConfig:
    hub_url: str
    token: str = ""
    device_uid: str = ""

    @staticmethod
    def path() -> Path:
        return data_dir() / "client.json"

    @classmethod
    def load(cls) -> ClientConfig | None:
        p = cls.path()
        if not p.exists():
            return None
        return cls(**json.loads(p.read_text(encoding="utf-8")))

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            p.chmod(0o600)


class HubError(Exception):
    pass


class HubClient:
    def __init__(self, hub_url: str, token: str | None = None, timeout: float = 120.0):
        headers = dict(CLIENT_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.base_url = hub_url.rstrip("/")
        self._c = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    @classmethod
    def from_config(cls) -> HubClient:
        cfg = ClientConfig.load()
        if cfg is None:
            raise HubError(
                "not paired with a hub; run `soulsaka hub login --url http://<hub>:8765 --code XXXX`"
            )
        return cls(cfg.hub_url, cfg.token)

    def close(self) -> None:
        self._c.close()

    # -- helpers -----------------------------------------------------------------------
    def _check(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise HubError(f"{r.request.method} {r.request.url.path}: {r.status_code} {detail}")
        return r.json() if r.content else None

    # -- endpoints ---------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._check(self._c.get("/api/health"))

    def pair(self, code: str, name: str, kind: str = "cli") -> PairResponse:
        data = self._check(
            self._c.post("/api/pair", json={"code": code, "name": name, "kind": kind})
        )
        return PairResponse(**data)

    def stats(self) -> StatsOut:
        return StatsOut(**self._check(self._c.get("/api/stats")))

    def push_messages(self, batch: MessageBatch) -> ImportReport:
        data = self._check(
            self._c.post(
                "/api/messages/batch",
                content=batch.model_dump_json(),
                headers={"content-type": "application/json"},
            )
        )
        return ImportReport(**data)

    def capture_text(
        self,
        text: str,
        *,
        uid: str | None = None,
        origin: str = "manual",
        client_ts: datetime | None = None,
    ) -> CaptureOut:
        body = {
            "uid": uid or new_uid(),
            "kind": "text",
            "origin": origin,
            "client_ts": to_iso(client_ts or utcnow()),
            "text": text,
        }
        return CaptureOut(**self._check(self._c.post("/api/captures", json=body)))

    def capture_audio(
        self,
        path: Path,
        *,
        uid: str | None = None,
        origin: str = "manual",
        client_ts: datetime | None = None,
        meta: dict | None = None,
    ) -> CaptureOut:
        path = Path(path)
        data = {
            "uid": uid or new_uid(),
            "client_ts": to_iso(client_ts or utcnow()),
            "origin": origin,
        }
        if meta:
            data["meta"] = json.dumps(meta)
        with path.open("rb") as fh:
            r = self._c.post(
                "/api/captures/audio",
                data=data,
                files={"file": (path.name, fh, "application/octet-stream")},
            )
        return CaptureOut(**self._check(r))

    def sync(self, since: str | None = None) -> SyncOut:
        params = {"since": since} if since else {}
        return SyncOut(**self._check(self._c.get("/api/sync", params=params)))

    def memories(self, q: str | None = None, limit: int = 50) -> list[MemoryOut]:
        params: dict[str, Any] = {"limit": limit}
        if q:
            params["q"] = q
        return [MemoryOut(**m) for m in self._check(self._c.get("/api/memories", params=params))]

    def add_memory(self, text: str, kind: str = "note") -> MemoryOut:
        return MemoryOut(
            **self._check(self._c.post("/api/memories", json={"text": text, "kind": kind}))
        )
