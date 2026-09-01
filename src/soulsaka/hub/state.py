"""Everything a request handler or job needs, in one object."""

from __future__ import annotations

import logging
from pathlib import Path

from soulsaka.config import Settings
from soulsaka.db import Database
from soulsaka.hub.events import EventBus
from soulsaka.identity import get_or_create_salt
from soulsaka.paths import audio_dir, data_dir, db_path, ensure_layout

log = logging.getLogger(__name__)


class HubState:
    def __init__(self, settings: Settings, db: Database | None = None):
        self.settings = settings
        self.root: Path = ensure_layout()
        self.db = db or Database(db_path())
        self.salt = get_or_create_salt(self.db)
        self.events = EventBus()
        self._services: dict[str, object] = {}

    # Lazily-built ML services. They import heavy libraries on first use only.
    def service(self, name: str):
        svc = self._services.get(name)
        if svc is None:
            from soulsaka.hub import services

            svc = services.build(name, self)
            self._services[name] = svc
        return svc

    def set_service(self, name: str, svc: object) -> None:
        """Inject a service (tests, or CLI tools sharing a preloaded model)."""
        self._services[name] = svc

    def abs_path(self, rel: str) -> Path:
        return data_dir() / rel

    def rel_path(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(data_dir().resolve()))

    def audio_path_for(self, uid: str, ext: str = "wav") -> Path:
        day = uid[:2]
        p = audio_dir() / day / f"{uid}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def close(self) -> None:
        self.db.close()
