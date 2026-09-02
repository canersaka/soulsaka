"""WhatsApp Desktop for Mac: ``ChatStorage.sqlite``.

Format notes
------------
The Mac app (a Catalyst build of the iOS app) keeps a Core Data store at
``~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite``.
Core Data names carry ``Z`` prefixes:

- ``ZWAMESSAGE``: ``ZTEXT``, ``ZISFROMME`` (authoritative for *me*), ``ZMESSAGEDATE``
  (seconds since 2001-01-01), ``ZCHATSESSION`` (-> ``ZWACHATSESSION.Z_PK``),
  ``ZFROMJID``, ``ZMESSAGETYPE`` (0 = text; everything else is media, calls or system
  events), ``ZGROUPMEMBER`` (-> ``ZWAGROUPMEMBER.Z_PK``, the sender inside a group),
  ``ZSTANZAID`` (the message id, when present).
- ``ZWACHATSESSION``: ``ZCONTACTJID``, ``ZPARTNERNAME`` (contact or group name),
  ``ZSESSIONTYPE`` (0 = 1:1, 1 = group, 2 = broadcast list, 3 = status).
- ``ZWAGROUPMEMBER``: ``ZMEMBERJID``, ``ZCONTACTNAME``.

JIDs look like ``905321234567@s.whatsapp.net`` (a person: the phone number) or
``1234567890-1600000000@g.us`` (a group). Person JIDs are turned back into ``+phone``
handles so the same person hashes identically across WhatsApp and iMessage.

WhatsApp Desktop on Windows keeps an encrypted store instead; use a chat export there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from soulsaka.importers.base import (
    FULL_DISK_ACCESS_HINT,
    DiscoveredSource,
    Importer,
    ImporterError,
    display_path,
    register_importer,
)
from soulsaka.importers.snapshot import SqliteSnapshot, apple_time, table_columns
from soulsaka.models import ImportedMessage

SESSION_GROUP = 1
SESSION_STATUS = 3
TEXT_MESSAGE = 0

_GROUP_CONTAINER = "group.net.whatsapp.WhatsApp.shared"


def jid_to_handle(jid: str | None) -> str | None:
    """``905321234567@s.whatsapp.net`` -> ``+905321234567``; groups/status -> None."""
    if not jid:
        return None
    user, _, domain = jid.partition("@")
    if domain == "s.whatsapp.net" and user.isdigit():
        return "+" + user
    if domain in ("g.us", "broadcast", "status") or not domain:
        return None
    return jid  # @lid and other opaque-but-stable identifiers


@dataclass
class _Session:
    external_id: str
    title: str | None
    is_group: bool
    handle: str | None  # the other person, for 1:1 chats
    name: str | None
    skip: bool


def _load_sessions(conn: sqlite3.Connection) -> dict[int, _Session]:
    sessions: dict[int, _Session] = {}
    for pk, jid, name, session_type in conn.execute(
        "SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE FROM ZWACHATSESSION"
    ):
        is_group = session_type == SESSION_GROUP
        handle = None if is_group else jid_to_handle(jid)
        sessions[pk] = _Session(
            external_id=jid or f"session:{pk}",
            title=name or handle or jid,
            is_group=is_group,
            handle=handle,
            name=None if is_group else name,
            skip=session_type == SESSION_STATUS,
        )
    return sessions


def _load_members(conn: sqlite3.Connection) -> dict[int, tuple[str | None, str | None]]:
    return {
        pk: (jid_to_handle(jid) or jid, name)
        for pk, jid, name in conn.execute(
            "SELECT Z_PK, ZMEMBERJID, ZCONTACTNAME FROM ZWAGROUPMEMBER"
        )
    }


@register_importer
class WhatsAppImporter(Importer):
    kind = "whatsapp"
    register = "text"
    label = "WhatsApp"

    @staticmethod
    def default_path(home: Path | None = None) -> Path:
        home = home or Path.home()
        return home / "Library" / "Group Containers" / _GROUP_CONTAINER / "ChatStorage.sqlite"

    def iter_messages(self) -> Iterator[ImportedMessage]:
        path = Path(self.locator)
        try:
            with SqliteSnapshot(path, prefix="soulsaka-whatsapp-") as conn:
                yield from self._iter_rows(conn)
        except PermissionError as e:
            raise ImporterError(f"cannot read {path}. {FULL_DISK_ACCESS_HINT}") from e
        except FileNotFoundError as e:
            raise ImporterError(f"not found: {path}") from e
        except sqlite3.Error as e:
            raise ImporterError(f"cannot open {path}: {e}") from e

    def _iter_rows(self, conn: sqlite3.Connection) -> Iterator[ImportedMessage]:
        sessions = _load_sessions(conn)
        members = _load_members(conn)
        cols = table_columns(conn, "ZWAMESSAGE")
        id_col = ", ZSTANZAID" if "ZSTANZAID" in cols else ", NULL AS ZSTANZAID"
        sql = (
            "SELECT Z_PK, ZTEXT, ZISFROMME, ZMESSAGEDATE, ZCHATSESSION, ZFROMJID, ZGROUPMEMBER"
            f"{id_col} FROM ZWAMESSAGE WHERE ZMESSAGETYPE = ? AND ZTEXT IS NOT NULL "
            "AND ZTEXT != '' ORDER BY Z_PK"
        )
        for row in conn.execute(sql, (TEXT_MESSAGE,)):
            session = sessions.get(row["ZCHATSESSION"])
            if session is None or session.skip:
                continue
            yield self._to_message(row, session, members)

    @staticmethod
    def _to_message(
        row: sqlite3.Row, session: _Session, members: dict[int, tuple[str | None, str | None]]
    ) -> ImportedMessage:
        is_me = bool(row["ZISFROMME"])
        handle = name = None
        if not is_me:
            member = members.get(row["ZGROUPMEMBER"]) if session.is_group else None
            if member is not None:
                handle, name = member
            else:
                handle = session.handle or jid_to_handle(row["ZFROMJID"])
                name = session.name
        return ImportedMessage(
            conversation_external_id=session.external_id,
            text=row["ZTEXT"],
            ts=apple_time(row["ZMESSAGEDATE"]),
            is_me=is_me,
            register="text",
            external_id=row["ZSTANZAID"] or str(row["Z_PK"]),
            conversation_title=session.title,
            is_group=session.is_group,
            sender_handle=handle,
            sender_name=name,
        )

    @classmethod
    def estimate(cls, path: Path) -> int:
        with SqliteSnapshot(path, prefix="soulsaka-whatsapp-") as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZMESSAGETYPE = ?", (TEXT_MESSAGE,)
                ).fetchone()[0]
            )

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        path = cls.default_path(home)
        if system == "Windows":
            reason = (
                "WhatsApp Desktop on Windows keeps an encrypted store; export chats from the "
                "phone (Settings → Chats → Export chat) and run `soulsaka import whatsapp-export`"
            )
            return [cls.found(path, available=False, reason=reason)]
        if system != "Darwin":
            return [cls.found(path, available=False, reason="only on macOS; run on the Mac")]
        try:
            if not path.is_file():
                return [
                    cls.found(
                        path, available=False, reason=f"not found: {display_path(path, home)}"
                    )
                ]
            estimate = cls.estimate(path)
        except PermissionError:
            return [cls.found(path, available=False, reason=FULL_DISK_ACCESS_HINT)]
        except (OSError, sqlite3.Error) as e:
            return [cls.found(path, available=False, reason=f"cannot open: {e}")]
        return [cls.found(path, estimate=estimate)]
