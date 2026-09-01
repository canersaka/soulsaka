"""Raw captures from clients."""

from __future__ import annotations

import json
from typing import Any

from soulsaka.db.connection import Database
from soulsaka.models import CaptureIn, CaptureOut
from soulsaka.util.time import now_iso, to_iso


def _row_to_out(db: Database, row) -> CaptureOut:
    d = dict(row)
    d.pop("id", None)
    d.pop("message_id", None)
    d.pop("audio_path", None)
    d.pop("meta", None)
    if d.get("speaker_is_me") is not None:
        d["speaker_is_me"] = bool(d["speaker_is_me"])
    d["memory_uids"] = [
        r[0]
        for r in db.all(
            "SELECT uid FROM memories WHERE source_ref = ? AND archived = 0 ORDER BY id",
            (row["uid"],),
        )
    ]
    return CaptureOut(**d)


def create_capture(
    db: Database,
    device_uid: str,
    cap: CaptureIn,
    *,
    audio_path: str | None = None,
    duration_s: float | None = None,
) -> tuple[CaptureOut, bool]:
    """Insert a capture. Returns (capture, created). Re-sends of the same uid are no-ops."""
    with db.tx() as conn:
        existing = conn.execute("SELECT * FROM captures WHERE uid = ?", (cap.uid,)).fetchone()
        if existing:
            return _row_to_out(db, existing), False
        conn.execute(
            """INSERT INTO captures(uid, device_uid, kind, origin, status, client_ts, received_at,
                                    text, audio_path, duration_s, meta)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
            (
                cap.uid,
                device_uid,
                cap.kind,
                cap.origin,
                to_iso(cap.client_ts),
                now_iso(),
                cap.text,
                audio_path,
                duration_s,
                json.dumps(cap.meta) if cap.meta else None,
            ),
        )
        row = conn.execute("SELECT * FROM captures WHERE uid = ?", (cap.uid,)).fetchone()
    return _row_to_out(db, row), True


def get_capture(db: Database, uid: str) -> CaptureOut | None:
    row = db.one("SELECT * FROM captures WHERE uid = ?", (uid,))
    return _row_to_out(db, row) if row else None


def get_capture_row(db: Database, uid: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM captures WHERE uid = ?", (uid,))
    return dict(row) if row else None


def list_captures(
    db: Database, *, since: str | None = None, limit: int = 50, status: str | None = None
) -> list[CaptureOut]:
    clauses, params = [], []
    if since:
        clauses.append("COALESCE(processed_at, received_at) > ?")
        params.append(since)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.all(
        f"SELECT * FROM captures {where} ORDER BY received_at DESC LIMIT ?", (*params, limit)
    )
    return [_row_to_out(db, r) for r in rows]


def update_capture(db: Database, uid: str, **fields: Any) -> None:
    if not fields:
        return
    if "meta" in fields and isinstance(fields["meta"], dict):
        fields["meta"] = json.dumps(fields["meta"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.tx() as conn:
        conn.execute(f"UPDATE captures SET {cols} WHERE uid = ?", (*fields.values(), uid))


def delete_capture(db: Database, uid: str) -> str | None:
    """Delete a capture; returns its audio path (relative) so the caller can remove the file."""
    with db.tx() as conn:
        row = conn.execute("SELECT audio_path FROM captures WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM captures WHERE uid = ?", (uid,))
        return row[0]
