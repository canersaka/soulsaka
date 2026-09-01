"""Who is who.

Other people's identifiers never hit the database in the clear: handles are salted
SHA-256 hashes. Whether a message is *me* is decided per source (iMessage and WhatsApp
know; exports and email are matched against the configured names/emails/phones).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from soulsaka.util.ids import new_token, sha256_hex

SALT_KEY = "identity.salt"

_PHONE_RE = re.compile(r"^\+?[\d\s().-]{6,}$")


def normalize_handle(handle: str) -> str:
    """Canonical form of a phone number, email or username before hashing."""
    h = handle.strip()
    if not h:
        return ""
    if "@" in h:
        return h.lower()
    if _PHONE_RE.match(h):
        digits = re.sub(r"\D", "", h)
        # Assume US numbers without a country code; other numbers keep what they have.
        if len(digits) == 10:
            digits = "1" + digits
        return "+" + digits
    return h.lower()


def handle_hash(salt: str, handle: str) -> str:
    return sha256_hex(salt, normalize_handle(handle))


def get_or_create_salt(db) -> str:
    salt = db.get_setting(SALT_KEY)
    if salt is None:
        salt = new_token()
        db.set_setting(SALT_KEY, salt)
    return salt


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().casefold())


@dataclass
class IdentityResolver:
    """Decides whether a sender is me, using the configured identity."""

    names: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._names = {_norm_name(n) for n in self.names if n.strip()}
        self._handles = {normalize_handle(e) for e in self.emails if e.strip()}
        self._handles |= {normalize_handle(p) for p in self.phones if p.strip()}

    @classmethod
    def from_settings(cls, settings) -> IdentityResolver:
        me = settings.me
        names = list(me.names)
        if me.display_name:
            names.append(me.display_name)
        return cls(names=names, emails=list(me.emails), phones=list(me.phones))

    @property
    def configured(self) -> bool:
        return bool(self._names or self._handles)

    def is_me_handle(self, handle: str | None) -> bool:
        return bool(handle) and normalize_handle(handle) in self._handles

    def is_me_name(self, name: str | None) -> bool:
        return bool(name) and _norm_name(name) in self._names

    def is_me(self, *, handle: str | None = None, name: str | None = None) -> bool:
        return self.is_me_handle(handle) or self.is_me_name(name)
