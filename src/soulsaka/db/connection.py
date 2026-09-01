from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from soulsaka.util.time import now_iso

_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def load_migrations() -> list[tuple[int, str, str]]:
    """Return (version, name, sql) for every packaged migration, ordered."""
    out: list[tuple[int, str, str]] = []
    pkg = resources.files("soulsaka.db") / "migrations"
    for entry in pkg.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if not m:
            continue
        out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    out.sort()
    return out


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    ran: list[str] = []
    for version, name, sql in load_migrations():
        if version in applied:
            continue
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations(version, name, applied_at) "
            f"VALUES ({version}, '{name}', '{now_iso()}');\n"
            "COMMIT;"
        )
        conn.executescript(script)
        ran.append(name)
    return ran


class Database:
    """Thread-safe handle on the SQLite file.

    Each thread gets its own connection (SQLite connections must not be shared across
    threads while in use). Writes are serialised through ``tx()`` so concurrent workers
    never fight over the write lock.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self.migrations_run = migrate(self.conn)

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _connect(self.path)
            self._local.conn = conn
            with self._all_lock:
                self._all.append(conn)
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Nested calls on the same thread join the outer one."""
        with self._write_lock:
            conn = self.conn
            if conn.in_transaction:
                yield conn
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def close(self) -> None:
        with self._all_lock:
            for conn in self._all:
                with contextlib.suppress(Exception):
                    conn.close()
            self._all.clear()
        self._local = threading.local()

    # Small helpers used all over the DAO modules.
    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple | dict = ()):
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    # Key/value settings table.
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else row[0]

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
