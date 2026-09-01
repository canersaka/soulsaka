"""Turn an utterance into memories.

Two layers:
  * a zero-latency rule pass that catches explicit requests ("remember this number
    is 4521", "hatırla ...", "don't forget ...") so a note is on every device within
    seconds of saying it;
  * an LLM pass, queued afterwards, that pulls durable facts and preferences out of
    ordinary speech and text (see hub/services/extract_llm.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TRIGGERS = [
    # English
    r"\bremember\b(?:\s+(?:this|that|to))?[:,]?\s*",
    r"\bnote\b(?:\s+(?:this|that|to self))?[:,]?\s*",
    r"\bdon'?t forget\b(?:\s+(?:this|that|to))?[:,]?\s*",
    r"\bdo not forget\b(?:\s+(?:this|that|to))?[:,]?\s*",
    r"\breminder\b[:,]?\s*",
    # Turkish
    r"\bhat[ıi]rla\b[:,]?\s*",
    r"\bnot al\b[:,]?\s*",
    r"\bunutma\b[:,]?\s*",
    r"\bbunu (?:not et|kaydet|hat[ıi]rla)\b[:,]?\s*",
]
_TRIGGER_RE = re.compile("|".join(f"(?:{t})" for t in _TRIGGERS), re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.])(?:\+?\d[\d\s().-]{2,}\d)(?![\w.])")
_TODO_RE = re.compile(
    r"\b(?:to do|todo|i need to|i have to|i should|remind me to|yapmam lazım|yapmalıyım)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|"
    r"bugün|yarın|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar|at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedMemory:
    text: str
    kind: str
    confidence: float = 1.0


def _classify(body: str) -> str:
    if _NUMBER_RE.search(body):
        return "number"
    if _TIME_RE.search(body):
        return "event"
    if _TODO_RE.search(body):
        return "todo"
    return "note"


def rule_extract(text: str) -> list[ExtractedMemory]:
    """Explicit 'remember ...' requests. Returns at most one memory per utterance."""
    t = " ".join(text.split())
    if not t:
        return []
    m = _TRIGGER_RE.search(t)
    if not m:
        return []
    body = t[m.end() :].strip(" ,.:;-")
    if len(body) < 2:
        body = t.strip()
    # "remember that my locker code is 4521" -> keep the whole statement, it reads better.
    return [ExtractedMemory(text=body[0].upper() + body[1:], kind=_classify(body))]
