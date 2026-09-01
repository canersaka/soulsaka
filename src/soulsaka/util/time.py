"""Time helpers. All timestamps stored by soulsaka are ISO-8601 strings in UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return to_iso(utcnow())


def to_iso(dt: datetime) -> str:
    """Render a datetime as an ISO-8601 UTC string with millisecond precision.

    Naive datetimes are assumed to already be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string (with or without ``Z``) into an aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def from_epoch(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)
