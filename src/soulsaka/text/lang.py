"""Cheap language guess for tagging. Only needs to separate Turkish from English well;
anything else returns None. Swap in `lingua` later if more languages show up."""

from __future__ import annotations

import re

_TR_CHARS = set("ğışİÇÖÜçöüĞŞ")
_TR_WORDS = {
    "ve",
    "bir",
    "bu",
    "için",
    "ama",
    "çok",
    "değil",
    "ne",
    "ben",
    "sen",
    "evet",
    "yok",
    "var",
    "mi",
    "mı",
    "mu",
    "mü",
    "da",
    "de",
    "ile",
    "gibi",
    "daha",
    "şey",
    "tamam",
    "iyi",
    "nasıl",
    "neden",
    "şimdi",
    "sonra",
    "biraz",
    "hadi",
    "abi",
    "canım",
    "olur",
    "yani",
    "işte",
    "kadar",
    "bende",
    "bana",
    "sana",
    "seni",
    "beni",
    "onu",
    "şu",
}
_EN_WORDS = {
    "the",
    "and",
    "is",
    "you",
    "that",
    "it",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "this",
    "have",
    "are",
    "was",
    "but",
    "not",
    "what",
    "just",
    "like",
    "yeah",
    "ok",
    "okay",
    "lol",
    "im",
    "i'm",
    "dont",
    "don't",
    "can",
    "will",
    "be",
    "so",
    "do",
    "me",
    "my",
    "your",
    "we",
    "they",
    "he",
    "she",
    "if",
    "or",
    "at",
    "from",
    "about",
    "got",
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def guess_lang(text: str) -> str | None:
    words = [w.casefold() for w in _WORD_RE.findall(text)]
    if not words:
        return None
    tr = sum(1 for w in words if w in _TR_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    tr_chars = sum(1 for ch in text if ch in _TR_CHARS)
    # Turkish-only letters are strong evidence; each counts like a stopword hit.
    tr_score = tr + tr_chars
    if tr_score == 0 and en == 0:
        return None
    if tr_score > en:
        return "tr"
    if en > tr_score:
        return "en"
    return "tr" if tr_chars else "en"
