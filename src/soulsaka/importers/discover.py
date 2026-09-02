"""Find message history on this machine without asking.

Each importer knows where its data lives per platform (``platform.system()`` names:
``Darwin``, ``Windows``, ``Linux``), whether it can be read right now (Full Disk Access
on macOS) and roughly how much is there. ``discover_all`` collects those answers so
``soulsaka import --auto`` can show a table and import everything that is available.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

import soulsaka.importers  # noqa: F401  (registers every importer)
from soulsaka.importers.base import IMPORTERS, DiscoveredSource

log = logging.getLogger(__name__)

# Importers with no fixed location (imap needs a login, docs needs a folder) are skipped.
DISCOVERY_ORDER = (
    "imessage",
    "whatsapp",
    "whatsapp_export",
    "emlx",
    "mbox",
    "discord",
    "git",
)


def discover_all(home: Path | None = None, system: str | None = None) -> list[DiscoveredSource]:
    home = Path(home) if home else Path.home()
    system = system or platform.system()
    found: list[DiscoveredSource] = []
    for kind in DISCOVERY_ORDER:
        cls = IMPORTERS.get(kind)
        if cls is None:
            continue
        try:
            found.extend(cls.discover(home, system))
        except Exception as e:  # noqa: BLE001 - one broken probe must not hide the others
            log.debug("discovery failed for %s", kind, exc_info=True)
            found.append(cls.found("", available=False, reason=f"discovery failed: {e}"))
    return found


def has_email_source(sources: list[DiscoveredSource]) -> bool:
    return any(s.kind == "email" and s.available for s in sources)
