"""Durable in-process job queue backed by the jobs table."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from soulsaka.db.connection import Database
from soulsaka.util.time import now_iso, to_iso, utcnow


@dataclass
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def enqueue(
    db: Database,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 0,
    delay_s: float = 0,
    max_attempts: int = 3,
) -> int:
    run_after = to_iso(utcnow() + timedelta(seconds=delay_s))
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT INTO jobs(kind, payload, status, priority, max_attempts, run_after, created_at)
               VALUES (?, ?, 'queued', ?, ?, ?, ?)""",
            (kind, json.dumps(payload or {}), priority, max_attempts, run_after, now_iso()),
        )
        return int(cur.lastrowid)


def claim(db: Database, kinds: list[str] | None = None) -> Job | None:
    """Atomically move the next runnable job to 'running'."""
    now = now_iso()
    kind_clause = ""
    params: list[Any] = [now]
    if kinds:
        kind_clause = " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        params.extend(kinds)
    with db.tx() as conn:
        row = conn.execute(
            f"""SELECT id, kind, payload, attempts, max_attempts FROM jobs
                WHERE status = 'queued' AND run_after <= ?{kind_clause}
                ORDER BY priority DESC, id LIMIT 1""",
            params,
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
            (now, row["id"]),
        )
        return Job(
            id=row["id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            attempts=row["attempts"] + 1,
            max_attempts=row["max_attempts"],
        )


def complete(db: Database, job_id: int) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', finished_at = ?, error = NULL WHERE id = ?",
            (now_iso(), job_id),
        )


def fail(db: Database, job: Job, error: str, *, backoff_s: float = 30.0) -> None:
    """Record a failure; retry later unless attempts are exhausted."""
    with db.tx() as conn:
        if job.attempts >= job.max_attempts:
            conn.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                (now_iso(), error[:2000], job.id),
            )
        else:
            delay = backoff_s * (2 ** (job.attempts - 1))
            conn.execute(
                "UPDATE jobs SET status = 'queued', run_after = ?, error = ? WHERE id = ?",
                (to_iso(utcnow() + timedelta(seconds=delay)), error[:2000], job.id),
            )


def requeue_stale(db: Database, older_than_s: float = 3600) -> int:
    """Jobs left 'running' by a crashed worker go back to the queue."""
    cutoff = to_iso(utcnow() - timedelta(seconds=older_than_s))
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued' WHERE status = 'running' AND started_at < ?",
            (cutoff,),
        )
        return cur.rowcount


def counts(db: Database) -> dict[str, int]:
    return {r[0]: r[1] for r in db.all("SELECT status, COUNT(*) FROM jobs GROUP BY status")}


def recent(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.all(
        "SELECT id, kind, status, attempts, created_at, started_at, finished_at, error FROM jobs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
