"""Versioned training runs (the ``training_runs`` table)."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from soulsaka.db import Database
from soulsaka.util.time import now_iso

_VERSION_RE = re.compile(r"^v(\d+)$")


def next_version(db: Database) -> str:
    rows = db.all("SELECT version FROM training_runs")
    nums = [int(m.group(1)) for r in rows if (m := _VERSION_RE.match(r[0]))]
    return f"v{(max(nums) + 1) if nums else 1}"


def create_run(
    db: Database,
    *,
    version: str,
    backend: str,
    base_model: str,
    config: dict[str, Any],
    status: str = "planned",
) -> dict[str, Any]:
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO training_runs(version, backend, base_model, status, config)
               VALUES (?, ?, ?, ?, ?)""",
            (version, backend, base_model, status, json.dumps(config)),
        )
    return get_run(db, version)  # type: ignore[return-value]


def update_run(db: Database, version: str, **fields: Any) -> None:
    for k in ("config", "metrics"):
        if k in fields and isinstance(fields[k], dict):
            fields[k] = json.dumps(fields[k])
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.tx() as conn:
        conn.execute(
            f"UPDATE training_runs SET {cols} WHERE version = ?", (*fields.values(), version)
        )


def _row(r) -> dict[str, Any]:
    d = dict(r)
    for k in ("config", "metrics"):
        if d.get(k):
            with contextlib.suppress(json.JSONDecodeError):
                d[k] = json.loads(d[k])
    d.pop("id", None)
    return d


def get_run(db: Database, version: str) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM training_runs WHERE version = ?", (version,))
    return _row(r) if r else None


def list_runs(db: Database) -> list[dict[str, Any]]:
    return [_row(r) for r in db.all("SELECT * FROM training_runs ORDER BY id")]


def latest_done(db: Database) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM training_runs WHERE status = 'done' ORDER BY id DESC LIMIT 1")
    return _row(r) if r else None


def mark_started(db: Database, version: str) -> None:
    update_run(db, version, status="running", started_at=now_iso(), error=None)


def mark_done(db: Database, version: str, **fields: Any) -> None:
    update_run(db, version, status="done", finished_at=now_iso(), **fields)


def mark_failed(db: Database, version: str, error: str) -> None:
    update_run(db, version, status="failed", finished_at=now_iso(), error=error[:2000])
