"""Prompt assembly and chat turns.

Two modes:
  twin      - reply *as* me: the adapter (if the profile serves one) plus style exemplars,
              memories and the self-model. Used for blind tests and "what would I say".
  assistant - help me: a plain assistant that knows my memories and context.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from soulsaka.db import captures as captures_db
from soulsaka.db import jobs as jobs_db
from soulsaka.hub.services import retrieval
from soulsaka.hub.state import HubState
from soulsaka.ml.llm import ChatMessage
from soulsaka.models import CaptureIn
from soulsaka.paths import data_dir
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso, utcnow

log = logging.getLogger(__name__)

SELF_MODEL_FILE = "self_model.md"

REGISTER_HINTS = {
    "text": "This is a text message conversation: short, casual, lowercase is fine, no sign-off.",
    "email": "This is an email: complete sentences, a greeting and a sign-off where natural.",
    "speech": "This is spoken conversation: reply the way it would be said out loud.",
    "doc": "This is written prose: paragraphs, considered wording.",
}


@dataclass
class ChatTurnOut:
    chat_uid: str
    text: str
    profile: str
    model: str


def self_model_text() -> str:
    p = data_dir() / SELF_MODEL_FILE
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _name(state: HubState) -> str:
    me = state.settings.me
    return me.display_name or (me.names[0] if me.names else "the user")


def build_messages(
    state: HubState,
    user_text: str,
    history: list[ChatMessage],
    *,
    mode: str = "assistant",
    register: str = "text",
    personal: bool = True,
) -> list[ChatMessage]:
    cfg = state.settings.llm
    name = _name(state)
    memories = retrieval.search_memories(state, user_text, k=cfg.retrieval_k)
    exemplars = (
        retrieval.search_exemplars(state, user_text, k=cfg.exemplar_k, register=register)
        if mode == "twin"
        else []
    )
    parts: list[str] = []
    if mode == "twin":
        parts.append(
            f"You are {name}. Reply exactly as {name} would, in {name}'s own voice, wording and "
            f"habits, in whatever language {name} would use here. Never mention being an AI. "
            f"Do not explain; just answer the way {name} answers."
        )
        parts.append(REGISTER_HINTS.get(register, ""))
        if not personal:
            parts.append(
                "You do not have fine-tuned weights for this person, so lean heavily on the "
                "examples of their real messages below: match their length, punctuation, slang "
                "and tone."
            )
    else:
        parts.append(
            f"You are {name}'s private personal assistant running on their own hardware. "
            f"Be concise and direct. Use the memories below when they are relevant and say "
            f"when you do not know something."
        )
        parts.append(f"Current time: {now_iso()}")
    sm = self_model_text()
    if sm:
        parts.append(f"## About {name}\n{sm}")
    if memories:
        parts.append("## Memories\n" + "\n".join(f"- {m.text}" for m in memories))
    if exemplars:
        parts.append(
            f"## Examples of how {name} actually writes\n"
            + "\n".join(f"- {e.text}" for e in exemplars)
        )
    system = "\n\n".join(p for p in parts if p)
    return [ChatMessage("system", system), *history, ChatMessage("user", user_text)]


def _chat_id(state: HubState, chat_uid: str | None, device_uid: str) -> tuple[int, str]:
    now = now_iso()
    with state.db.tx() as conn:
        if chat_uid:
            row = conn.execute("SELECT id FROM chats WHERE uid = ?", (chat_uid,)).fetchone()
            if row:
                conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, row[0]))
                return int(row[0]), chat_uid
        chat_uid = chat_uid or new_uid()
        cur = conn.execute(
            "INSERT INTO chats(uid, device_uid, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (chat_uid, device_uid, now, now),
        )
        return int(cur.lastrowid), chat_uid


def history_for(state: HubState, chat_id: int, limit: int) -> list[ChatMessage]:
    rows = state.db.all(
        "SELECT role, text FROM chat_turns WHERE chat_id = ? AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    )
    return [ChatMessage(r["role"], r["text"]) for r in reversed(rows)]


def _store_turn(
    state: HubState, chat_id: int, role: str, text: str, profile: str | None = None
) -> int:
    with state.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO chat_turns(chat_id, role, text, profile, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, text, profile, now_iso()),
        )
        return int(cur.lastrowid)


def _capture_user_turn(state: HubState, device_uid: str, text: str) -> None:
    """My side of a chat is still my writing; keep it, tagged as chat so training can
    down-weight it (talking to a bot is a narrow register)."""
    cap = CaptureIn(uid=new_uid(), kind="text", origin="chat", client_ts=utcnow(), text=text)
    _, created = captures_db.create_capture(state.db, device_uid, cap)
    if created:
        jobs_db.enqueue(state.db, "process_capture", {"uid": cap.uid})


def respond(
    state: HubState,
    *,
    text: str,
    device_uid: str,
    chat_uid: str | None = None,
    profile: str | None = None,
    mode: str = "assistant",
    register: str = "text",
    stream: bool = False,
) -> Iterator[str] | ChatTurnOut:
    llm = state.service("llm")
    prof_name, prof = llm.profile(profile)
    chat_id, chat_uid = _chat_id(state, chat_uid, device_uid)
    history = history_for(state, chat_id, state.settings.llm.max_history)
    messages = build_messages(
        state, text, history, mode=mode, register=register, personal=prof.personal
    )
    _store_turn(state, chat_id, "user", text)
    _capture_user_turn(state, device_uid, text)

    if not stream:
        resp = llm.complete(messages, profile=prof_name)
        _store_turn(state, chat_id, "assistant", resp.text, prof_name)
        return ChatTurnOut(chat_uid=chat_uid, text=resp.text, profile=prof_name, model=resp.model)

    def gen() -> Iterator[str]:
        pieces: list[str] = []
        try:
            for piece in llm.stream(messages, profile=prof_name):
                pieces.append(piece)
                yield piece
        finally:
            full = "".join(pieces).strip()
            if full:
                _store_turn(state, chat_id, "assistant", full, prof_name)

    return gen()


def list_chats(state: HubState, limit: int = 50) -> list[dict]:
    rows = state.db.all(
        """SELECT c.uid, c.title, c.created_at, c.updated_at,
                  (SELECT text FROM chat_turns t WHERE t.chat_id = c.id ORDER BY t.id LIMIT 1) AS first_text
           FROM chats c ORDER BY c.updated_at DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def chat_turns(state: HubState, chat_uid: str) -> list[dict]:
    rows = state.db.all(
        """SELECT t.role, t.text, t.profile, t.created_at FROM chat_turns t
           JOIN chats c ON c.id = t.chat_id WHERE c.uid = ? ORDER BY t.id""",
        (chat_uid,),
    )
    return [dict(r) for r in rows]
