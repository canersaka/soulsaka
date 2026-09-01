"""LLM pass that turns ordinary utterances into durable memories."""

from __future__ import annotations

import logging
import re

from soulsaka.db import captures as captures_db
from soulsaka.db import jobs as jobs_db
from soulsaka.db import memories as memories_db
from soulsaka.hub.state import HubState
from soulsaka.ml.llm import ChatMessage, LLMError, extract_json
from soulsaka.text.normalize import word_count

log = logging.getLogger(__name__)

KINDS = {"note", "fact", "preference", "todo", "number", "event", "person"}

SYSTEM = """You maintain a private memory for one person, extracted from things they said or wrote.
Extract only durable, specific facts about them that would still matter weeks later:
preferences, plans and dates, people and relationships, numbers and codes, decisions, tasks.
Skip small talk, opinions about the weather, anything already obvious, and anything about
other people that the speaker is merely relaying.
Write each memory as one short sentence in the same language the person used, in third person
("Prefers ...", "Has a dentist appointment on ..."). Never invent details.
Reply with JSON only: {"memories": [{"text": "...", "kind": "note|fact|preference|todo|number|event|person", "confidence": 0.0-1.0}]}
Reply {"memories": []} if there is nothing worth keeping."""


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def _too_similar(a: str, b: str) -> bool:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return False
    j = len(ta & tb) / len(ta | tb)
    return j >= 0.7


def extract_from_text(
    state: HubState, text: str, source_ref: str, *, min_confidence: float = 0.5
) -> list[str]:
    if word_count(text) < 4:
        return []
    llm = state.service("llm")
    if not llm.available():
        log.debug("llm unavailable; skipping extraction for %s", source_ref)
        return []
    try:
        resp = llm.complete(
            [ChatMessage("system", SYSTEM), ChatMessage("user", text)],
            json_mode=True,
            temperature=0.1,
            max_tokens=400,
        )
        data = extract_json(resp.text)
    except (LLMError, ValueError) as e:
        log.warning("memory extraction failed for %s: %s", source_ref, e)
        return []
    items = data.get("memories") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    created: list[str] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        mtext = str(item.get("text") or "").strip()
        if len(mtext) < 4:
            continue
        kind = item.get("kind") if item.get("kind") in KINDS else "fact"
        try:
            conf = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        if conf < min_confidence:
            continue
        existing = memories_db.search_memories(state.db, mtext, limit=5)
        if any(_too_similar(mtext, e.text) for e in existing):
            continue
        out, was_created = memories_db.create_memory(
            state.db,
            mtext,
            kind=kind,
            source_kind="extracted",
            source_ref=source_ref,
            confidence=conf,
        )
        if was_created:
            created.append(out.uid)
            jobs_db.enqueue(state.db, "embed_memory", {"uid": out.uid}, priority=-2)
            state.events.publish("memory", uid=out.uid, kind=out.kind, text=out.text)
    return created


def extract_from_capture(state: HubState, uid: str) -> list[str]:
    cap = captures_db.get_capture_row(state.db, uid)
    if not cap or not cap.get("text") or cap.get("speaker_is_me") == 0:
        return []
    return extract_from_text(state, cap["text"], uid)
