"""Read a live SQLite database safely: copy it first, then open the copy read-only.

Messages.app and WhatsApp keep their databases open in WAL mode. Reading the original
while it is being written risks ``database is locked`` errors and, worse, partial reads
of the WAL. Copying the main file plus its ``-wal`` and ``-shm`` sidecars into a private
temporary directory gives a consistent snapshot that we can query at leisure and delete
afterwards.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

SIDECAR_SUFFIXES = ("-wal", "-shm")

# Both Messages.app and WhatsApp's Core Data store count from 2001-01-01T00:00:00Z.
APPLE_EPOCH = 978_307_200
_NANOSECOND_THRESHOLD = 10**11  # seconds since 2001 stay far below this; nanoseconds far above


def apple_time(value: float | int | None) -> datetime:
    """Seconds (or, on macOS >= 10.13 for iMessage, nanoseconds) since 2001 -> aware UTC."""
    v = float(value or 0)
    if abs(v) > _NANOSECOND_THRESHOLD:
        v /= 1e9
    return datetime.fromtimestamp(APPLE_EPOCH + v, tz=UTC)


def copy_sqlite(source: Path, into: Path) -> Path:
    """Copy ``source`` and its WAL/SHM sidecars into ``into``; return the copied path."""
    target = into / source.name
    shutil.copy2(source, target)
    for suffix in SIDECAR_SUFFIXES:
        sidecar = source.with_name(source.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, into / sidecar.name)
    return target


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open a private copy read-only.

    A copied WAL-mode database still has un-checkpointed frames in its ``-wal`` file;
    SQLite reads those fine in ``mode=ro`` as long as it can create the ``-shm`` next to
    it, which it can in our temp dir. Should a build refuse anyway, fall back to a normal
    open: the copy is ours, so nothing of the user's can be touched.
    """
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(path))
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class SqliteSnapshot:
    """``with SqliteSnapshot(path) as conn:`` -- a read-only connection to a private copy.

    Raises ``PermissionError`` when the file cannot be read (on macOS: no Full Disk
    Access) and ``sqlite3.OperationalError`` when the copy is not a usable database.
    """

    def __init__(self, source: Path, prefix: str = "soulsaka-"):
        self.source = Path(source)
        self.prefix = prefix
        self.tempdir: Path | None = None
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        if not self.source.is_file():
            raise FileNotFoundError(str(self.source))
        # Touch the file before copying so a TCC denial surfaces as PermissionError.
        with self.source.open("rb") as fh:
            fh.read(16)
        self.tempdir = Path(tempfile.mkdtemp(prefix=self.prefix))
        try:
            copied = copy_sqlite(self.source, self.tempdir)
            self.conn = open_readonly(copied)
        except BaseException:
            self.close()
            raise
        return self.conn

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.tempdir is not None:
            shutil.rmtree(self.tempdir, ignore_errors=True)
            self.tempdir = None
