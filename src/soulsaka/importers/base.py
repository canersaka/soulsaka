"""Common ground for every importer.

An importer is a generator over one local data source. ``run_import`` pulls from it in
chunks and hands each chunk to a sink, so a multi-gigabyte mailbox or chat database
never has to fit in memory. Discovery is a classmethod on each importer so that
``soulsaka import --auto`` can ask every importer "where would your data be on this
machine, and can we read it?" without constructing one.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from soulsaka.identity import IdentityResolver
from soulsaka.models import ImportedMessage, ImportReport, Register, SourceRef

CHUNK_SIZE = 2000

FULL_DISK_ACCESS_HINT = (
    "Grant Full Disk Access to your terminal: "
    "System Settings → Privacy & Security → Full Disk Access"
)

# Folders people drop exports into. Searched a couple of levels deep by discovery.
EXPORT_DIR_NAMES = ("Downloads", "Desktop", "Documents")


class ImporterError(Exception):
    """A problem the user has to fix: missing permission, unknown identity, bad path."""


@dataclass
class DiscoveredSource:
    """One place an importer could read from, and whether it can right now."""

    kind: str  # source kind as stored in the sources table: imessage, whatsapp, email, ...
    label: str
    locator: str
    available: bool
    reason: str | None = None  # why it is unavailable, or a note when it is
    estimate: int | None = None  # rough number of messages, when cheap to know
    importer_kind: str = ""  # key into IMPORTERS


class Sink(Protocol):
    """Where imported messages go. See :mod:`soulsaka.importers.sinks`."""

    def write(self, source: SourceRef, messages: list[ImportedMessage]) -> ImportReport: ...


class Importer:
    """Base class. Subclasses set the class attributes and implement ``iter_messages``.

    ``locator`` is whatever identifies the source on disk (a path, an account); it ends
    up in the sources table so re-imports of the same thing are idempotent.
    """

    kind: ClassVar[str] = ""  # registry key and CLI command name
    source_kind: ClassVar[str] = ""  # sources.kind; defaults to ``kind``
    register: ClassVar[Register] = "text"
    label: ClassVar[str] = ""

    def __init__(
        self, locator: str | os.PathLike[str] = "", *, identity: IdentityResolver | None = None
    ) -> None:
        self.locator = str(locator)
        self.identity = identity or IdentityResolver()
        self.notes: list[str] = []

    def source_ref(self) -> SourceRef:
        return SourceRef(kind=self.source_kind or self.kind, label=self.label, locator=self.locator)

    def iter_messages(self) -> Iterator[ImportedMessage]:
        raise NotImplementedError

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        """Known locations of this kind of data on ``system`` (platform.system() names)."""
        return []

    def note(self, text: str) -> None:
        """Record something the user should see in the final report."""
        self.notes.append(f"{self.label}: {text}")

    @classmethod
    def found(
        cls,
        locator: str | os.PathLike[str],
        *,
        available: bool = True,
        reason: str | None = None,
        estimate: int | None = None,
        label: str | None = None,
    ) -> DiscoveredSource:
        return DiscoveredSource(
            kind=cls.source_kind or cls.kind,
            label=label or cls.label,
            locator=str(locator),
            available=available,
            reason=reason,
            estimate=estimate,
            importer_kind=cls.kind,
        )


IMPORTERS: dict[str, type[Importer]] = {}


def register_importer(cls: type[Importer]) -> type[Importer]:
    """Class decorator: make an importer available to discovery and the CLI."""
    if not cls.kind:
        raise ValueError(f"{cls.__name__} has no kind")
    IMPORTERS[cls.kind] = cls
    return cls


def run_import(
    importer: Importer,
    sink: Sink,
    progress: Callable[[int], None] | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> ImportReport:
    """Stream ``importer`` into ``sink`` in chunks and return the merged report.

    ``progress`` is called with the running number of messages handed to the sink.
    ``conversations`` in the result is the number of distinct conversations streamed.
    """
    source = importer.source_ref()
    report = ImportReport(source=source)
    seen_conversations: set[str] = set()
    chunk: list[ImportedMessage] = []
    sent = 0

    def flush() -> None:
        nonlocal sent
        if not chunk:
            return
        report.merge(sink.write(source, list(chunk)))
        sent += len(chunk)
        chunk.clear()
        if progress is not None:
            progress(sent)

    for message in importer.iter_messages():
        seen_conversations.add(message.conversation_external_id)
        chunk.append(message)
        if len(chunk) >= chunk_size:
            flush()
    flush()
    report.conversations = len(seen_conversations)
    report.notes.extend(importer.notes)
    return report


# --- helpers shared by importers ------------------------------------------------------


def search_dirs(home: Path) -> list[Path]:
    """Where discovery looks for dropped-in exports: ~/Downloads, ~/Desktop, ~/Documents."""
    return [home / name for name in EXPORT_DIR_NAMES if (home / name).is_dir()]


def find_paths(
    roots: Iterable[Path], match: Callable[[Path], bool], *, max_depth: int = 3, limit: int = 50
) -> list[Path]:
    """Breadth-limited walk returning paths (files or directories) that ``match``.

    Hidden entries are skipped, symlinks are not followed, and unreadable directories are
    ignored. A matching directory is not descended into.
    """
    out: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if len(out) >= limit:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            path = Path(entry.path)
            if match(path):
                out.append(path)
                if len(out) >= limit:
                    return
                continue
            if entry.is_dir() and depth < max_depth:
                walk(path, depth + 1)

    for root in roots:
        if root.is_dir():
            walk(root, 1)
    return out


def display_path(path: str | os.PathLike[str], home: Path | None = None) -> str:
    """``/Users/x/Downloads/a.txt`` -> ``~/Downloads/a.txt`` for tables and hints."""
    home = home or Path.home()
    p = Path(path)
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        return str(p)
