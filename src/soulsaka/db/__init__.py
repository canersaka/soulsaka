"""SQLite storage. One file, WAL mode, plain SQL, numbered migrations."""

from soulsaka.db.connection import Database

__all__ = ["Database"]
