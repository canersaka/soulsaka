"""Text cleanup shared by importers, captures and the dataset builder."""

from __future__ import annotations

import re
import unicodedata

# Placeholders exported by chat apps in place of media.
_MEDIA_PLACEHOLDERS = (
    "<media omitted>",
    "<medya dahil edilmedi>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "gif omitted",
    "document omitted",
    "contact card omitted",
    "this message was deleted",
    "you deleted this message",
    "bu mesaj silindi",
    "missed voice call",
    "missed video call",
    "‎",
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿‎‏"), None)
_OBJ_REPLACEMENT = "￼"
_WS_RE = re.compile(r"[ \t ]+")
_NL_RE = re.compile(r"\n{3,}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\S+")
_EMOJI_ONLY_RE = re.compile(r"^[\s\U0001F300-\U0001FAFF☀-➿⬀-⯿️‍\U0001F1E6-\U0001F1FF!?.,]+$")


def clean_text(text: str) -> str:
    """Normalise unicode and whitespace without changing wording."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = t.translate(_ZERO_WIDTH).replace(_OBJ_REPLACEMENT, "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(_WS_RE.sub(" ", line).strip() for line in t.split("\n"))
    t = _NL_RE.sub("\n\n", t)
    return t.strip()


def word_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def strip_urls(text: str) -> str:
    return _URL_RE.sub("", text).strip()


def low_signal_reason(text: str) -> str | None:
    """Why a message carries no style signal, or None if it is fine."""
    t = text.strip()
    if not t:
        return "empty"
    low = t.casefold()
    if any(p in low for p in _MEDIA_PLACEHOLDERS) and word_count(strip_urls(t)) <= 4:
        return "media_placeholder"
    if not strip_urls(t):
        return "url_only"
    if _EMOJI_ONLY_RE.match(t):
        return "emoji_only"
    return None


def collapse_burst(texts: list[str]) -> str:
    """Join consecutive messages from the same sender into one target."""
    return "\n".join(t.strip() for t in texts if t.strip())
