"""What happens to a capture after it lands on the hub.

text capture  -> corpus message (register=text) -> rule memories -> LLM memory job
audio capture -> speaker check -> transcript -> corpus message (register=speech)
              -> rule memories -> LLM memory job
Audio that is not the enrolled speaker is handled by the privacy policy.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import Any

from soulsaka.db import captures as captures_db
from soulsaka.db import corpus as corpus_db
from soulsaka.db import jobs as jobs_db
from soulsaka.db import memories as memories_db
from soulsaka.hub.services.memory_extract import rule_extract
from soulsaka.hub.state import HubState
from soulsaka.models import ImportedMessage, SourceRef
from soulsaka.text.lang import guess_lang
from soulsaka.text.normalize import clean_text
from soulsaka.util.time import now_iso, parse_iso

log = logging.getLogger(__name__)

CAPTURE_SOURCE = SourceRef(kind="capture", label="Captures", locator="captures")


class SpeakerRejected(Exception):
    pass


def store_as_message(state: HubState, cap: dict[str, Any], text: str, register: str) -> int | None:
    """Append my utterance to the corpus. Returns the message id."""
    device = cap["device_uid"]
    msg = ImportedMessage(
        conversation_external_id=f"device:{device}",
        conversation_title=f"Captures from {device}",
        text=text,
        ts=parse_iso(cap["client_ts"]),
        is_me=True,
        register=register,  # type: ignore[arg-type]
        external_id=cap["uid"],
        meta={"capture": cap["uid"], "origin": cap["origin"]},
    )
    corpus_db.ingest_messages(
        state.db,
        state.salt,
        CAPTURE_SOURCE,
        [msg],
        keep_names=state.settings.privacy.keep_contact_names,
    )
    row = state.db.one(
        "SELECT id FROM messages WHERE external_id = ? ORDER BY id DESC LIMIT 1", (cap["uid"],)
    )
    return int(row[0]) if row else None


def extract_and_store_memories(state: HubState, cap_uid: str, text: str) -> list[str]:
    uids: list[str] = []
    for mem in rule_extract(text):
        out, created = memories_db.create_memory(
            state.db,
            mem.text,
            kind=mem.kind,
            source_kind="explicit",
            source_ref=cap_uid,
            confidence=mem.confidence,
        )
        if created:
            uids.append(out.uid)
            state.events.publish("memory", uid=out.uid, kind=out.kind, text=out.text)
    return uids


def transcribe(state: HubState, cap: dict[str, Any]) -> tuple[str, str | None, float | None]:
    asr = state.service("asr")
    audio = state.abs_path(cap["audio_path"])
    result = asr.transcribe(audio, language=state.settings.asr.language)
    return result.text, result.language, result.duration_s


def verify_speaker(state: HubState, cap: dict[str, Any]) -> tuple[bool | None, float | None]:
    """Returns (is_me, score). is_me is None when nobody is enrolled yet."""
    speaker = state.service("speaker")
    audio = state.abs_path(cap["audio_path"])
    return speaker.verify(state.db, audio)


def process_capture(state: HubState, uid: str) -> None:
    cap = captures_db.get_capture_row(state.db, uid)
    if cap is None:
        return
    if cap["status"] == "done":
        return
    captures_db.update_capture(state.db, uid, status="processing", error=None)
    try:
        if cap["kind"] == "text":
            text = clean_text(cap["text"] or "")
            register = "text"
            is_me: bool | None = True
            score: float | None = None
            lang = guess_lang(text)
        else:
            policy = state.settings.privacy
            is_me, score = verify_speaker(state, cap)
            if is_me is False and policy.other_speakers == "discard":
                _discard(state, uid, "other speaker", score)
                return
            text, lang, duration = transcribe(state, cap)
            text = clean_text(text)
            if duration is not None:
                captures_db.update_capture(state.db, uid, duration_s=duration)
            register = "speech"
            if not text:
                _discard(state, uid, "no speech", score)
                return
            if is_me is False:
                # context_only: keep the transcript on the capture, never in the corpus.
                captures_db.update_capture(
                    state.db,
                    uid,
                    status="done",
                    processed_at=now_iso(),
                    text=text,
                    lang=lang,
                    speaker_is_me=0,
                    speaker_score=score,
                )
                state.events.publish("capture", uid=uid, status="done", speaker_is_me=False)
                return
        message_id = store_as_message(state, cap, text, register) if text else None
        memory_uids = extract_and_store_memories(state, uid, text) if text else []
        captures_db.update_capture(
            state.db,
            uid,
            status="done",
            processed_at=now_iso(),
            text=text,
            lang=lang,
            speaker_is_me=None if is_me is None else int(is_me),
            speaker_score=score,
            message_id=message_id,
        )
        # Nobody enrolled yet: manual push-to-talk captures are by definition me,
        # so use them to build the voice profile.
        if text and cap["kind"] == "audio" and is_me is None and cap["origin"] == "manual":
            jobs_db.enqueue(state.db, "enroll_speaker", {"uid": uid}, priority=1)
        if text:
            jobs_db.enqueue(state.db, "extract_memories_llm", {"uid": uid}, priority=-1)
            jobs_db.enqueue(state.db, "embed_message", {"message_id": message_id}, priority=-2)
        state.events.publish(
            "capture",
            uid=uid,
            status="done",
            text=text,
            memory_uids=memory_uids,
            speaker_is_me=is_me,
        )
    except Exception as e:  # noqa: BLE001 - recorded on the row, retried by the queue
        log.exception("capture %s failed", uid)
        captures_db.update_capture(
            state.db, uid, status="pending", error=f"{type(e).__name__}: {e}"
        )
        raise


def _discard(state: HubState, uid: str, reason: str, score: float | None) -> None:
    cap = captures_db.get_capture_row(state.db, uid)
    if cap and cap.get("audio_path"):
        with contextlib.suppress(OSError):
            state.abs_path(cap["audio_path"]).unlink(missing_ok=True)
    captures_db.update_capture(
        state.db,
        uid,
        status="discarded",
        processed_at=now_iso(),
        error=reason,
        speaker_is_me=0 if reason == "other speaker" else None,
        speaker_score=score,
        audio_path=None,
    )
    state.events.publish("capture", uid=uid, status="discarded", reason=reason)


def _dt(value: str) -> datetime:
    return parse_iso(value)
