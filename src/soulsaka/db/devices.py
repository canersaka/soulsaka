"""Devices and pairing codes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from soulsaka.db.connection import Database
from soulsaka.util.ids import hash_token, new_pairing_code, new_token, new_uid
from soulsaka.util.time import now_iso, parse_iso, to_iso, utcnow


@dataclass(frozen=True)
class Device:
    uid: str
    name: str
    kind: str
    created_at: str = ""
    last_seen_at: str | None = None


LOCAL_DEVICE = Device(uid="local", name="this machine", kind="cli")


def create_pairing_code(db: Database, ttl_s: int = 600) -> str:
    code = new_pairing_code()
    expires = to_iso(utcnow() + timedelta(seconds=ttl_s))
    with db.tx() as conn:
        conn.execute("DELETE FROM pairing_codes WHERE expires_at < ?", (now_iso(),))
        conn.execute("INSERT INTO pairing_codes(code, expires_at) VALUES (?, ?)", (code, expires))
    return code


def redeem_pairing_code(db: Database, code: str, name: str, kind: str) -> tuple[Device, str] | None:
    code = code.strip().upper()
    with db.tx() as conn:
        row = conn.execute(
            "SELECT expires_at, used_at FROM pairing_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None or row["used_at"] is not None or parse_iso(row["expires_at"]) < utcnow():
            return None
        conn.execute("UPDATE pairing_codes SET used_at = ? WHERE code = ?", (now_iso(), code))
        device, token = _create_device(conn, name, kind)
    return device, token


def create_device(db: Database, name: str, kind: str) -> tuple[Device, str]:
    """Create a device directly (CLI on the hub machine, tests)."""
    with db.tx() as conn:
        return _create_device(conn, name, kind)


def _create_device(conn, name: str, kind: str) -> tuple[Device, str]:
    token = new_token()
    uid = new_uid()
    created = now_iso()
    conn.execute(
        "INSERT INTO devices(uid, name, kind, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, name, kind, hash_token(token), created),
    )
    return Device(uid=uid, name=name, kind=kind, created_at=created), token


def device_by_token(db: Database, token: str) -> Device | None:
    row = db.one(
        "SELECT uid, name, kind, created_at, last_seen_at FROM devices WHERE token_hash = ?",
        (hash_token(token),),
    )
    return Device(**dict(row)) if row else None


def touch_device(db: Database, uid: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE uid = ?", (now_iso(), uid))


def list_devices(db: Database) -> list[Device]:
    rows = db.all("SELECT uid, name, kind, created_at, last_seen_at FROM devices ORDER BY id")
    return [Device(**dict(r)) for r in rows]


def revoke_device(db: Database, uid: str) -> bool:
    with db.tx() as conn:
        return conn.execute("DELETE FROM devices WHERE uid = ?", (uid,)).rowcount == 1
