"""Pydantic types shared by the hub API, the client and the importers."""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# "register" (text | email | speech | doc) is the domain term used throughout. pydantic warns
# because BaseModel's metaclass exposes ABCMeta.register; an instance field never conflicts.
warnings.filterwarnings("ignore", message='Field name "register"', category=UserWarning)

Register = Literal["text", "email", "speech", "doc"]
DeviceKind = Literal["browser", "listener", "importer", "cli"]
CaptureKind = Literal["text", "audio"]
CaptureOrigin = Literal["manual", "listener", "chat"]
MemoryKind = Literal["note", "fact", "preference", "todo", "number", "event", "person"]


# --- devices / pairing -------------------------------------------------------------


class PairRequest(BaseModel):
    code: str
    name: str = "device"
    kind: DeviceKind = "browser"


class PairResponse(BaseModel):
    device_uid: str
    token: str


class DeviceOut(BaseModel):
    uid: str
    name: str
    kind: str
    created_at: str
    last_seen_at: str | None = None


# --- corpus ------------------------------------------------------------------------


class SourceRef(BaseModel):
    kind: str
    label: str
    locator: str = ""


class ImportedMessage(BaseModel):
    """One message as produced by an importer. Plain, source-agnostic."""

    conversation_external_id: str
    text: str
    ts: datetime
    is_me: bool
    register: Register = "text"
    external_id: str | None = None
    conversation_title: str | None = None
    is_group: bool = False
    sender_handle: str | None = None
    sender_name: str | None = None
    meta: dict[str, Any] | None = None


class MessageBatch(BaseModel):
    source: SourceRef
    messages: list[ImportedMessage]


class ImportReport(BaseModel):
    source: SourceRef
    received: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    me_words: int = 0
    conversations: int = 0
    notes: list[str] = Field(default_factory=list)

    def merge(self, other: ImportReport) -> None:
        self.received += other.received
        self.inserted += other.inserted
        self.duplicates += other.duplicates
        self.skipped += other.skipped
        self.me_words += other.me_words
        self.conversations = max(self.conversations, other.conversations)
        for k, v in other.skipped_reasons.items():
            self.skipped_reasons[k] = self.skipped_reasons.get(k, 0) + v
        self.notes.extend(other.notes)


class SourceOut(BaseModel):
    id: int
    kind: str
    label: str
    locator: str
    device_uid: str
    created_at: str
    last_import_at: str | None
    messages: int
    me_messages: int
    me_words: int


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    is_me: bool
    ts: str
    register: str
    lang: str | None
    text: str
    word_count: int
    sender_name: str | None = None


# --- captures ----------------------------------------------------------------------


class CaptureIn(BaseModel):
    uid: str
    kind: CaptureKind = "text"
    origin: CaptureOrigin = "manual"
    client_ts: datetime
    text: str | None = None
    meta: dict[str, Any] | None = None


class CaptureOut(BaseModel):
    uid: str
    device_uid: str
    kind: str
    origin: str
    status: str
    client_ts: str
    received_at: str
    processed_at: str | None = None
    text: str | None = None
    lang: str | None = None
    duration_s: float | None = None
    speaker_is_me: bool | None = None
    speaker_score: float | None = None
    error: str | None = None
    memory_uids: list[str] = Field(default_factory=list)


# --- memories ----------------------------------------------------------------------


class MemoryIn(BaseModel):
    text: str
    kind: MemoryKind = "note"
    uid: str | None = None
    expires_at: datetime | None = None
    meta: dict[str, Any] | None = None


class MemoryUpdate(BaseModel):
    text: str | None = None
    kind: MemoryKind | None = None
    archived: bool | None = None
    expires_at: datetime | None = None


class MemoryOut(BaseModel):
    uid: str
    kind: str
    text: str
    source_kind: str
    source_ref: str | None
    confidence: float
    created_at: str
    updated_at: str
    expires_at: str | None
    archived: bool
    score: float | None = None


# --- sync / stats ------------------------------------------------------------------


class SyncOut(BaseModel):
    server_time: str
    memories: list[MemoryOut]
    captures: list[CaptureOut]


class RegisterStats(BaseModel):
    register: str
    messages: int
    words: int


class SourceStats(BaseModel):
    kind: str
    label: str
    messages: int
    words: int


class MonthStats(BaseModel):
    month: str
    words: int


class StatsOut(BaseModel):
    me_words: int
    me_messages: int
    other_messages: int
    conversations: int
    memories: int
    captures_pending: int
    by_register: list[RegisterStats]
    by_source: list[SourceStats]
    by_lang: dict[str, int]
    by_month: list[MonthStats]
    first_train_threshold: int = 30_000
    comfortable_threshold: int = 50_000
    ready_for_first_train: bool
    latest_version: str | None = None
