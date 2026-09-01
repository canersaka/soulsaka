"""Sources, contacts, conversations and messages."""

from __future__ import annotations

import json
from collections.abc import Iterable

from soulsaka.db.connection import Database
from soulsaka.identity import handle_hash
from soulsaka.models import (
    ImportedMessage,
    ImportReport,
    MessageOut,
    MonthStats,
    RegisterStats,
    SourceOut,
    SourceRef,
    SourceStats,
    StatsOut,
)
from soulsaka.text.lang import guess_lang
from soulsaka.text.normalize import clean_text, low_signal_reason, word_count
from soulsaka.util.ids import sha256_hex
from soulsaka.util.time import now_iso, to_iso

ME_HANDLE = "__me__"


def get_or_create_source(db: Database, ref: SourceRef, device_uid: str = "") -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE kind = ? AND locator = ? AND device_uid = ?",
            (ref.kind, ref.locator, device_uid),
        ).fetchone()
        if row:
            conn.execute("UPDATE sources SET label = ? WHERE id = ?", (ref.label, row[0]))
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO sources(kind, label, locator, device_uid, created_at) VALUES (?, ?, ?, ?, ?)",
            (ref.kind, ref.label, ref.locator, device_uid, now_iso()),
        )
        return int(cur.lastrowid)


def get_or_create_contact(
    db: Database, salt: str, *, handle: str | None, name: str | None, is_me: bool, keep_names: bool
) -> int | None:
    if is_me:
        key = handle_hash(salt, ME_HANDLE)
        display = None
    elif handle:
        key = handle_hash(salt, handle)
        display = name if keep_names else None
    elif name:
        key = sha256_hex(salt, "name", name.strip().casefold())
        display = name if keep_names else None
    else:
        return None
    with db.tx() as conn:
        row = conn.execute(
            "SELECT id, display_name FROM contacts WHERE handle_hash = ?", (key,)
        ).fetchone()
        if row:
            if display and not row[1]:
                conn.execute("UPDATE contacts SET display_name = ? WHERE id = ?", (display, row[0]))
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO contacts(handle_hash, display_name, is_me) VALUES (?, ?, ?)",
            (key, display, 1 if is_me else 0),
        )
        return int(cur.lastrowid)


def get_or_create_conversation(
    db: Database, source_id: int, external_id: str, title: str | None, is_group: bool
) -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT id, title FROM conversations WHERE source_id = ? AND external_id = ?",
            (source_id, external_id),
        ).fetchone()
        if row:
            if title and row[1] != title:
                conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, row[0]))
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO conversations(source_id, external_id, title, is_group) VALUES (?, ?, ?, ?)",
            (source_id, external_id, title, 1 if is_group else 0),
        )
        return int(cur.lastrowid)


def message_content_hash(ts_iso: str, text: str) -> str:
    return sha256_hex(ts_iso, text)


def ingest_messages(
    db: Database,
    salt: str,
    source: SourceRef,
    messages: Iterable[ImportedMessage],
    *,
    device_uid: str = "",
    keep_names: bool = True,
) -> ImportReport:
    """Insert a batch of imported messages. Idempotent: re-importing is a no-op."""
    report = ImportReport(source=source)
    source_id = get_or_create_source(db, source, device_uid)
    conv_cache: dict[str, int] = {}
    contact_cache: dict[tuple, int | None] = {}
    touched_convs: set[int] = set()

    with db.tx() as conn:
        for m in messages:
            report.received += 1
            text = clean_text(m.text)
            reason = low_signal_reason(text)
            if reason:
                report.skipped += 1
                report.skipped_reasons[reason] = report.skipped_reasons.get(reason, 0) + 1
                continue
            conv_key = m.conversation_external_id
            conv_id = conv_cache.get(conv_key)
            if conv_id is None:
                conv_id = get_or_create_conversation(
                    db, source_id, conv_key, m.conversation_title, m.is_group
                )
                conv_cache[conv_key] = conv_id
            ckey = (m.is_me, m.sender_handle, m.sender_name)
            if ckey not in contact_cache:
                contact_cache[ckey] = get_or_create_contact(
                    db,
                    salt,
                    handle=m.sender_handle,
                    name=m.sender_name,
                    is_me=m.is_me,
                    keep_names=keep_names,
                )
            contact_id = contact_cache[ckey]
            ts_iso = to_iso(m.ts)
            wc = word_count(text)
            cur = conn.execute(
                """INSERT OR IGNORE INTO messages
                   (conversation_id, contact_id, is_me, ts, register, lang, text, word_count,
                    external_id, content_hash, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv_id,
                    contact_id,
                    1 if m.is_me else 0,
                    ts_iso,
                    m.register,
                    guess_lang(text),
                    text,
                    wc,
                    m.external_id,
                    message_content_hash(ts_iso, text),
                    json.dumps(m.meta) if m.meta else None,
                ),
            )
            if cur.rowcount == 1:
                report.inserted += 1
                touched_convs.add(conv_id)
                if m.is_me:
                    report.me_words += wc
            else:
                report.duplicates += 1
        conn.execute("UPDATE sources SET last_import_at = ? WHERE id = ?", (now_iso(), source_id))
    report.conversations = len(touched_convs)
    return report


def list_sources(db: Database) -> list[SourceOut]:
    rows = db.all(
        """SELECT s.id, s.kind, s.label, s.locator, s.device_uid, s.created_at, s.last_import_at,
                  COUNT(m.id) AS messages,
                  COALESCE(SUM(m.is_me), 0) AS me_messages,
                  COALESCE(SUM(CASE WHEN m.is_me = 1 THEN m.word_count ELSE 0 END), 0) AS me_words
           FROM sources s
           LEFT JOIN conversations c ON c.source_id = s.id
           LEFT JOIN messages m ON m.conversation_id = c.id
           GROUP BY s.id ORDER BY s.id"""
    )
    return [SourceOut(**dict(r)) for r in rows]


def delete_source(db: Database, source_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return cur.rowcount == 1


def search_messages(
    db: Database, query: str, *, limit: int = 20, me_only: bool = True
) -> list[MessageOut]:
    where = "AND m.is_me = 1" if me_only else ""
    rows = db.all(
        f"""SELECT m.id, m.conversation_id, m.is_me, m.ts, m.register, m.lang, m.text, m.word_count,
                   c.display_name AS sender_name
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            LEFT JOIN contacts c ON c.id = m.contact_id
            WHERE messages_fts MATCH ? {where}
            ORDER BY bm25(messages_fts) LIMIT ?""",
        (fts_query(query), limit),
    )
    return [MessageOut(**{**dict(r), "is_me": bool(r["is_me"])}) for r in rows]


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: quoted terms OR-ed together."""
    terms = [t.replace('"', "") for t in text.split()]
    terms = [t for t in terms if t]
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms)


def stats(db: Database) -> StatsOut:
    me_words = int(
        db.scalar("SELECT COALESCE(SUM(word_count), 0) FROM messages WHERE is_me = 1") or 0
    )
    me_messages = int(db.scalar("SELECT COUNT(*) FROM messages WHERE is_me = 1") or 0)
    other_messages = int(db.scalar("SELECT COUNT(*) FROM messages WHERE is_me = 0") or 0)
    conversations = int(db.scalar("SELECT COUNT(*) FROM conversations") or 0)
    memories = int(db.scalar("SELECT COUNT(*) FROM memories WHERE archived = 0") or 0)
    pending = int(
        db.scalar("SELECT COUNT(*) FROM captures WHERE status IN ('pending', 'processing')") or 0
    )
    by_register = [
        RegisterStats(register=r[0], messages=r[1], words=r[2])
        for r in db.all(
            "SELECT register, COUNT(*), SUM(word_count) FROM messages WHERE is_me = 1 GROUP BY register ORDER BY 3 DESC"
        )
    ]
    by_source = [
        SourceStats(kind=r[0], label=r[1], messages=r[2], words=r[3])
        for r in db.all(
            """SELECT s.kind, s.label, COUNT(m.id), COALESCE(SUM(m.word_count), 0)
               FROM sources s
               JOIN conversations c ON c.source_id = s.id
               JOIN messages m ON m.conversation_id = c.id AND m.is_me = 1
               GROUP BY s.id ORDER BY 4 DESC"""
        )
    ]
    by_lang = {
        (r[0] or "unknown"): r[1]
        for r in db.all("SELECT lang, SUM(word_count) FROM messages WHERE is_me = 1 GROUP BY lang")
    }
    by_month = [
        MonthStats(month=r[0], words=r[1])
        for r in db.all(
            "SELECT substr(ts, 1, 7) AS month, SUM(word_count) FROM messages WHERE is_me = 1 GROUP BY month ORDER BY month"
        )
    ]
    latest = db.scalar(
        "SELECT version FROM training_runs WHERE status = 'done' ORDER BY id DESC LIMIT 1"
    )
    return StatsOut(
        me_words=me_words,
        me_messages=me_messages,
        other_messages=other_messages,
        conversations=conversations,
        memories=memories,
        captures_pending=pending,
        by_register=by_register,
        by_source=by_source,
        by_lang=by_lang,
        by_month=by_month,
        ready_for_first_train=me_words >= 30_000,
        latest_version=latest,
    )
