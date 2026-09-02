"""iMessage: ``~/Library/Messages/chat.db``.

Format notes
------------
``chat.db`` is Messages.app's SQLite store (WAL mode, protected by Full Disk Access).

- ``message``: one row per message. ``text`` holds the body on older systems; since
  macOS Ventura it is usually NULL and the body lives in ``attributedBody``, a
  "typedstream" archive of an NSAttributedString. ``is_from_me`` is authoritative for
  *me*. ``date`` is nanoseconds since 2001-01-01 (seconds on macOS < 10.13; the two are
  told apart by magnitude). ``item_type != 0`` rows are group events, rows with
  ``associated_message_type != 0`` are tapbacks/stickers/edits, and
  ``balloon_bundle_id`` marks app messages (Apple Pay, games, polls). Plain link
  previews use the URL balloon and are kept, since the message text is the link.
- ``handle``: the phone numbers / emails (``id``) of the people I talk to.
- ``chat``: conversations; ``style`` 43 is a group, 45 a 1:1 chat; ``display_name`` is
  the group name if one was set; ``guid`` is stable across machines.
- ``chat_message_join`` / ``chat_handle_join``: which messages and handles belong to
  which chat.

typedstream: inside ``attributedBody`` the UTF-8 text follows the class name
``NSString`` and the marker bytes ``01 94 84 01 2b``. The next byte is the length, unless
it is ``0x81`` (a little-endian uint16 length follows) or ``0x82`` (uint32).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
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

GROUP_STYLE = 43
URL_BALLOON = "com.apple.messages.URLBalloonProvider"

_TYPEDSTREAM_CLASS = b"NSString"
_TYPEDSTREAM_MARK = b"\x01\x94\x84\x01\x2b"


def attributed_body_text(blob: bytes | None) -> str | None:
    """Pull the plain text out of a typedstream ``attributedBody`` blob."""
    if not blob:
        return None
    start = blob.find(_TYPEDSTREAM_CLASS)
    if start < 0:
        return None
    mark = blob.find(_TYPEDSTREAM_MARK, start)
    if mark < 0:
        return None
    pos = mark + len(_TYPEDSTREAM_MARK)
    if pos >= len(blob):
        return None
    head = blob[pos]
    pos += 1
    if head == 0x81:
        length = int.from_bytes(blob[pos : pos + 2], "little")
        pos += 2
    elif head == 0x82:
        length = int.from_bytes(blob[pos : pos + 4], "little")
        pos += 4
    else:
        length = head
    raw = blob[pos : pos + length]
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace")


@dataclass
class _Chat:
    external_id: str
    title: str | None
    is_group: bool


def _load_chats(conn: sqlite3.Connection) -> dict[int, _Chat]:
    """chat ROWID -> conversation identity. Title is the group name, else the other handle."""
    participants: dict[int, list[str]] = defaultdict(list)
    for chat_id, handle in conn.execute(
        "SELECT chj.chat_id, h.id FROM chat_handle_join chj "
        "JOIN handle h ON h.ROWID = chj.handle_id ORDER BY chj.chat_id, h.ROWID"
    ):
        participants[chat_id].append(handle)
    has_style = "style" in table_columns(conn, "chat")
    style_col = "style" if has_style else "NULL"
    chats: dict[int, _Chat] = {}
    for rowid, guid, identifier, display_name, style in conn.execute(
        f"SELECT ROWID, guid, chat_identifier, display_name, {style_col} FROM chat"
    ):
        handles = participants.get(rowid, [])
        is_group = (style == GROUP_STYLE) if style is not None else len(handles) > 1
        title = display_name or (handles[0] if len(handles) == 1 else identifier)
        chats[rowid] = _Chat(external_id=guid or f"chat:{rowid}", title=title, is_group=is_group)
    return chats


def _filters(cols: set[str]) -> list[str]:
    where: list[str] = []
    if "item_type" in cols:
        where.append("m.item_type = 0")
    if "associated_message_type" in cols:
        where.append("m.associated_message_type = 0")
    if "balloon_bundle_id" in cols:
        where.append(
            "(m.balloon_bundle_id IS NULL OR m.balloon_bundle_id = '' "
            f"OR m.balloon_bundle_id = '{URL_BALLOON}')"
        )
    return where


def _message_sql(cols: set[str]) -> str:
    body = "m.attributedBody" if "attributedBody" in cols else "NULL"
    sql = (
        "SELECT m.ROWID AS rowid, m.guid AS guid, m.text AS text, m.is_from_me AS is_from_me, "
        f"m.date AS date, h.id AS handle, cmj.chat_id AS chat_id, {body} AS body "
        "FROM message m "
        "LEFT JOIN handle h ON h.ROWID = m.handle_id "
        "LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID"
    )
    where = _filters(cols)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + " ORDER BY m.ROWID"


def _count_sql(cols: set[str]) -> str:
    where = _filters(cols)
    sql = "SELECT COUNT(*) FROM message m"
    return sql + (" WHERE " + " AND ".join(where) if where else "")


@register_importer
class IMessageImporter(Importer):
    kind = "imessage"
    register = "text"
    label = "iMessage"

    @staticmethod
    def default_path(home: Path | None = None) -> Path:
        return (home or Path.home()) / "Library" / "Messages" / "chat.db"

    def iter_messages(self) -> Iterator[ImportedMessage]:
        path = Path(self.locator)
        try:
            with SqliteSnapshot(path, prefix="soulsaka-imessage-") as conn:
                yield from self._iter_rows(conn)
        except PermissionError as e:
            raise ImporterError(f"cannot read {path}. {FULL_DISK_ACCESS_HINT}") from e
        except FileNotFoundError as e:
            raise ImporterError(f"not found: {path}") from e
        except sqlite3.Error as e:
            raise ImporterError(f"cannot open {path}: {e}") from e

    def _iter_rows(self, conn: sqlite3.Connection) -> Iterator[ImportedMessage]:
        chats = _load_chats(conn)
        cols = table_columns(conn, "message")
        last_rowid: int | None = None
        for row in conn.execute(_message_sql(cols)):
            if row["rowid"] == last_rowid:  # a message joined to several chats: keep the first
                continue
            last_rowid = row["rowid"]
            text = row["text"] or attributed_body_text(row["body"])
            if not text or not text.strip():
                continue
            yield self._to_message(row, text, chats.get(row["chat_id"]))

    @staticmethod
    def _to_message(row: sqlite3.Row, text: str, chat: _Chat | None) -> ImportedMessage:
        is_me = bool(row["is_from_me"])
        handle = row["handle"]
        if chat is None:
            conversation = f"handle:{handle}" if handle else "unknown"
            title, is_group = handle, False
        else:
            conversation, title, is_group = chat.external_id, chat.title, chat.is_group
        return ImportedMessage(
            conversation_external_id=conversation,
            text=text,
            ts=apple_time(row["date"]),
            is_me=is_me,
            register="text",
            external_id=row["guid"],
            conversation_title=title,
            is_group=is_group,
            sender_handle=None if is_me else handle,
        )

    @classmethod
    def estimate(cls, path: Path) -> int:
        with SqliteSnapshot(path, prefix="soulsaka-imessage-") as conn:
            cols = table_columns(conn, "message")
            return int(conn.execute(_count_sql(cols)).fetchone()[0])

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        path = cls.default_path(home)
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
