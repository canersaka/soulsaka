"""Importers: turn message history that already exists on a machine into corpus rows.

Each importer streams :class:`soulsaka.models.ImportedMessage` objects from one kind of
source (a chat database, an export file, a mail store, a git checkout) and
:func:`soulsaka.importers.base.run_import` drains that stream into a sink, chunk by chunk.
The sink is either the local database (when running on the hub) or the hub's HTTP API.

Importing this package registers every importer in :data:`soulsaka.importers.base.IMPORTERS`.
"""

from __future__ import annotations

from soulsaka.importers import (  # noqa: F401  (registration side effects)
    discord,
    docs,
    emlx,
    git,
    imap,
    imessage,
    mbox,
    whatsapp,
    whatsapp_export,
)
from soulsaka.importers.base import (
    IMPORTERS,
    DiscoveredSource,
    Importer,
    ImporterError,
    run_import,
)

__all__ = [
    "IMPORTERS",
    "DiscoveredSource",
    "Importer",
    "ImporterError",
    "run_import",
]
