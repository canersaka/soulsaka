"""Memories: searchable facts the assistant should know."""

from __future__ import annotations

import json
from typing import Any

from soulsaka.db.connection import Database
from soulsaka.db.corpus import fts_query
from soulsaka.models import MemoryOut
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso


def _to_out(row, score: float | None = None) -> MemoryOut:
    d = dict(row)
    d.pop("id", None)
    d.pop("meta", None)
    d["archived"] = bool(d["archived"])
    return MemoryOut(**d, score=score)


def create_memory(
    db: Database,
    text: str,
    *,
    kind: str = "note",
    source_kind: str = "manual",
    source_ref: str | None = None,
    uid: str | None = None,
    confidence: float = 1.0,
    expires_at: str | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[MemoryOut, bool]:
    uid = uid or new_uid()
    now = now_iso()
    with db.tx() as conn:
        existing = conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()
        if existing:
            return _to_out(existing), False
        conn.execute(
            """INSERT INTO memories(uid, kind, text, source_kind, source_ref, confidence,
                                    created_at, updated_at, expires_at, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid,
                kind,
                text.strip(),
                source_kind,
                source_ref,
                confidence,
                now,
                now,
                expires_at,
                json.dumps(meta) if meta else None,
            ),
        )
        row = conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()
    return _to_out(row), True


def get_memory(db: Database, uid: str) -> MemoryOut | None:
    row = db.one("SELECT * FROM memories WHERE uid = ?", (uid,))
    return _to_out(row) if row else None


def update_memory(db: Database, uid: str, **fields: Any) -> MemoryOut | None:
    fields = {k: v for k, v in fields.items() if v is not None}
    if "archived" in fields:
        fields["archived"] = 1 if fields["archived"] else 0
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.tx() as conn:
        conn.execute(f"UPDATE memories SET {cols} WHERE uid = ?", (*fields.values(), uid))
        row = conn.execute("SELECT * FROM memories WHERE uid = ?", (uid,)).fetchone()
    return _to_out(row) if row else None


def delete_memory(db: Database, uid: str) -> bool:
    with db.tx() as conn:
        return conn.execute("DELETE FROM memories WHERE uid = ?", (uid,)).rowcount == 1


def list_memories(
    db: Database,
    *,
    since: str | None = None,
    limit: int = 100,
    include_archived: bool = False,
    kind: str | None = None,
) -> list[MemoryOut]:
    clauses, params = [], []
    if since:
        clauses.append("updated_at > ?")
        params.append(since)
    if not include_archived:
        clauses.append("archived = 0")
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.all(
        f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)
    )
    return [_to_out(r) for r in rows]


def search_memories(db: Database, query: str, *, limit: int = 20) -> list[MemoryOut]:
    rows = db.all(
        """SELECT m.*, bm25(memories_fts) AS rank
           FROM memories_fts f JOIN memories m ON m.id = f.rowid
           WHERE memories_fts MATCH ? AND m.archived = 0
           ORDER BY rank LIMIT ?""",
        (fts_query(query), limit),
    )
    out = []
    for r in rows:
        d = dict(r)
        rank = d.pop("rank")
        out.append(_to_out(d, score=-float(rank)))
    return out


def memory_ids_by_uid(db: Database, uids: list[str]) -> dict[str, int]:
    if not uids:
        return {}
    marks = ",".join("?" for _ in uids)
    return {
        r[0]: r[1]
        for r in db.all(f"SELECT uid, id FROM memories WHERE uid IN ({marks})", tuple(uids))
    }
