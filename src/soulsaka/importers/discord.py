"""Discord data package (Settings → Privacy & Safety → Request all of my data).

Format notes
------------
The package (``package.zip`` or the extracted folder) contains ``messages/index.json``,
mapping channel id to a display name (``"Direct Message with alice"``,
``"general in Some Server"``, or null for channels that no longer exist), and one folder
per channel: ``messages/c<id>/`` on recent packages, ``messages/<id>/`` on older ones.
Each folder has ``channel.json`` (``type`` 1 = DM, 3 = group DM, otherwise a server
channel, plus ``recipients``/``name``) and either ``messages.json`` -- a list of
``{"ID", "Timestamp", "Contents", "Attachments"}`` -- or, on older packages,
``messages.csv`` with the same columns. Timestamps are ``2024-01-02 15:45:12.123000+00:00``.

Only my own messages are in the package, so everything is *me*.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import (
    DiscoveredSource,
    Importer,
    ImporterError,
    display_path,
    find_paths,
    register_importer,
    search_dirs,
)
from soulsaka.models import ImportedMessage

DM_TYPE = 1
GROUP_DM_TYPE = 3


class _Package:
    """Uniform access to ``messages/...`` inside a zip or an extracted directory."""

    def __init__(self, path: Path):
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._base = "messages"
        if path.is_file():
            self._zip = zipfile.ZipFile(path)
            index = next(
                (n for n in self._zip.namelist() if n.endswith("messages/index.json")), None
            )
            if index is None:
                raise ImporterError(f"{path}: no messages/index.json inside")
            self._base = str(PurePosixPath(index).parent)
        elif (path / "messages" / "index.json").is_file():
            self._base = "messages"
        elif (path / "index.json").is_file():
            self.path, self._base = path.parent, path.name
        else:
            raise ImporterError(f"{path}: no messages/index.json found")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def read(self, rel: str) -> str | None:
        name = f"{self._base}/{rel}"
        if self._zip is not None:
            try:
                return self._zip.read(name).decode("utf-8-sig", errors="replace")
            except KeyError:
                return None
        p = self.path / name
        return p.read_text(encoding="utf-8-sig", errors="replace") if p.is_file() else None

    def channel_dirs(self) -> list[str]:
        if self._zip is not None:
            prefix = self._base + "/"
            names = {
                n[len(prefix) :].split("/", 1)[0]
                for n in self._zip.namelist()
                if n.startswith(prefix) and "/" in n[len(prefix) :]
            }
            return sorted(n for n in names if n)
        base = self.path / self._base
        return sorted(p.name for p in base.iterdir() if p.is_dir())


def parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _rows_from_json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _rows_from_csv(text: str) -> list[dict[str, Any]]:
    return list(csv.DictReader(io.StringIO(text)))


def _title_and_group(
    channel_id: str, index_name: str | None, channel: dict[str, Any] | None
) -> tuple[str, bool]:
    ctype = channel.get("type") if channel else None
    if channel and not index_name:
        recipients = channel.get("recipients") or []
        name = channel.get("name") or ", ".join(str(r) for r in recipients)
        index_name = name or None
    title = index_name or f"channel {channel_id}"
    if ctype is not None:
        return title, ctype != DM_TYPE
    return title, not title.lower().startswith("direct message")


@register_importer
class DiscordImporter(Importer):
    kind = "discord"
    register = "text"
    label = "Discord"

    def __init__(self, locator: str | Path, *, identity: IdentityResolver | None = None) -> None:
        super().__init__(locator, identity=identity)
        self.source_label = f"{self.label} {Path(locator).name}"

    def iter_messages(self) -> Iterator[ImportedMessage]:
        path = Path(self.locator)
        if not path.exists():
            raise ImporterError(f"not found: {path}")
        package = _Package(path)
        try:
            index = json.loads(package.read("index.json") or "{}")
            for folder in package.channel_dirs():
                yield from self._iter_channel(package, folder, index)
        finally:
            package.close()

    def _iter_channel(
        self, package: _Package, folder: str, index: dict[str, Any]
    ) -> Iterator[ImportedMessage]:
        channel_id = folder[1:] if folder.startswith("c") and folder[1:].isdigit() else folder
        channel_json = package.read(f"{folder}/channel.json")
        channel = json.loads(channel_json) if channel_json else None
        title, is_group = _title_and_group(channel_id, index.get(channel_id), channel)
        rows: list[dict[str, Any]] = []
        text = package.read(f"{folder}/messages.json")
        if text is not None:
            rows = _rows_from_json(text)
        else:
            text = package.read(f"{folder}/messages.csv")
            rows = _rows_from_csv(text) if text is not None else []
        for row in rows:
            contents = (row.get("Contents") or "").strip()
            ts = parse_timestamp(str(row.get("Timestamp") or ""))
            if not contents or ts is None:
                continue
            yield ImportedMessage(
                conversation_external_id=channel_id,
                text=contents,
                ts=ts,
                is_me=True,
                register="text",
                external_id=str(row.get("ID") or "") or None,
                conversation_title=title,
                is_group=is_group,
            )

    # -- discovery ------------------------------------------------------------------
    @staticmethod
    def looks_like_package(path: Path) -> bool:
        if path.is_dir():
            return (path / "messages" / "index.json").is_file()
        if path.suffix.lower() != ".zip" or not path.name.lower().startswith(
            ("package", "discord")
        ):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                return any(n.endswith("messages/index.json") for n in zf.namelist())
        except (OSError, zipfile.BadZipFile):
            return False

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        return [
            cls.found(path, label=f"Discord package {display_path(path, home)}")
            for path in find_paths(search_dirs(home), cls.looks_like_package)
        ]
