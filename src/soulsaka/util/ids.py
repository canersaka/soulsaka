"""Identifier and token helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid


def new_uid() -> str:
    return uuid.uuid4().hex


def new_token() -> str:
    """A device bearer token. Only its SHA-256 hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_pairing_code() -> str:
    """Short human-typeable pairing code (no ambiguous characters)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def sha256_hex(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()
