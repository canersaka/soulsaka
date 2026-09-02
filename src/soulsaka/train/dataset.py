"""Build a training snapshot from the corpus.

Every example is a chat: a system prompt (who I am, the register, the setting), a
window of prior turns, and one assistant turn that is a burst of *my* messages. Other
people's messages appear only inside user turns as context. Holdout is split by
conversation so evaluation never sees a conversation that was trained on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from soulsaka.config import Settings, TrainConfig
from soulsaka.db import Database
from soulsaka.paths import datasets_dir
from soulsaka.text.normalize import low_signal_reason, word_count
from soulsaka.train.prompting import standalone_instruction, system_prompt
from soulsaka.util.time import now_iso, parse_iso

log = logging.getLogger(__name__)

BURST_GAP = timedelta(minutes=20)
CONTEXT_MAX_AGE = timedelta(days=3)
APPROX_CHARS_PER_TOKEN = 4


@dataclass
class Turn:
    role: str  # user | assistant
    text: str
    ts: str
    names: list[str] = field(default_factory=list)


@dataclass
class Example:
    messages: list[dict[str, str]]
    meta: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"messages": self.messages, "meta": self.meta}, ensure_ascii=False)


@dataclass
class DatasetStats:
    n_examples: int = 0
    n_holdout: int = 0
    n_words: int = 0
    by_register: dict[str, int] = field(default_factory=dict)
    by_lang: dict[str, int] = field(default_factory=dict)
    conversations: int = 0
    data_cutoff: str | None = None
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


@dataclass
class Manifest:
    version: str
    created_at: str
    base_model: str
    data_cutoff: str | None
    n_examples: int
    n_holdout: int
    n_words: int
    by_register: dict[str, int]
    by_lang: dict[str, int]
    conversations: int
    skipped: dict[str, int]
    train_sha256: str
    config: dict[str, Any]


def _display_name(settings: Settings) -> str:
    me = settings.me
    return me.display_name or (me.names[0] if me.names else "me")


def _conversation_rows(db: Database, cutoff: str | None) -> list[dict[str, Any]]:
    clause = "AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.is_me = 1)"
    rows = db.all(
        f"""SELECT c.id, c.title, c.is_group, s.kind AS source_kind, s.label AS source_label
            FROM conversations c JOIN sources s ON s.id = c.source_id
            WHERE 1=1 {clause} ORDER BY c.id"""
    )
    return [dict(r) for r in rows]


def _messages(db: Database, conversation_id: int, cutoff: str | None) -> list[dict[str, Any]]:
    params: list[Any] = [conversation_id]
    clause = ""
    if cutoff:
        clause = "AND m.ts <= ?"
        params.append(cutoff)
    rows = db.all(
        f"""SELECT m.id, m.is_me, m.ts, m.register, m.lang, m.text, m.word_count, m.meta,
                   ct.display_name AS sender_name
            FROM messages m LEFT JOIN contacts ct ON ct.id = m.contact_id
            WHERE m.conversation_id = ? {clause} ORDER BY m.ts, m.id""",
        tuple(params),
    )
    return [dict(r) for r in rows]


def _origin(meta: str | None) -> str | None:
    if not meta:
        return None
    try:
        return json.loads(meta).get("origin")
    except (json.JSONDecodeError, AttributeError):
        return None


def build_turns(messages: list[dict[str, Any]], *, is_group: bool) -> list[Turn]:
    """Merge consecutive messages from the same side into turns."""
    turns: list[Turn] = []
    for m in messages:
        role = "assistant" if m["is_me"] else "user"
        name = m.get("sender_name") or "Them"
        text = m["text"].strip()
        if not text:
            continue
        if turns and turns[-1].role == role:
            prev = turns[-1]
            gap = parse_iso(m["ts"]) - parse_iso(prev.ts)
            if gap <= BURST_GAP:
                if role == "user" and is_group and name not in prev.names:
                    prev.names.append(name)
                    prev.text += f"\n{name}: {text}"
                elif role == "user" and is_group:
                    prev.text += f"\n{name}: {text}"
                else:
                    prev.text += f"\n{text}"
                prev.ts = m["ts"]
                continue
        turns.append(
            Turn(
                role=role,
                text=f"{name}: {text}" if (role == "user" and is_group) else text,
                ts=m["ts"],
                names=[name] if role == "user" else [],
            )
        )
    return turns


def _fit_context(turns: list[Turn], budget_chars: int) -> list[Turn]:
    out: list[Turn] = []
    used = 0
    for t in reversed(turns):
        used += len(t.text) + 16
        if used > budget_chars and out:
            break
        out.append(t)
    return list(reversed(out))


def _setting(conv: dict[str, Any], turn_names: list[str]) -> str:
    kind = conv["source_kind"]
    title = conv.get("title") or ""
    if kind in ("email", "email_mbox", "email_emlx", "email_imap"):
        return f"email thread{': ' + title if title else ''}"
    if conv.get("is_group"):
        return f"group chat{': ' + title if title else ''}"
    other = ", ".join(n for n in turn_names if n and n != "Them")[:80]
    return f"1:1 {kind} conversation" + (f" with {other}" if other else "")


def _holdout(conversation_id: int, fraction: float, seed: int) -> bool:
    h = hashlib.sha256(f"{seed}:{conversation_id}".encode()).digest()
    return (int.from_bytes(h[:4], "little") % 10_000) < int(fraction * 10_000)


def iter_examples(
    db: Database,
    settings: Settings,
    *,
    cutoff: str | None = None,
    stats: DatasetStats | None = None,
) -> Iterator[tuple[Example, bool]]:
    """Yield (example, is_holdout) for the whole corpus up to ``cutoff``."""
    cfg: TrainConfig = settings.train
    name = _display_name(settings)
    stats = stats if stats is not None else DatasetStats()
    seen: set[str] = set()
    budget = cfg.max_seq_len * APPROX_CHARS_PER_TOKEN - 600
    convs = _conversation_rows(db, cutoff)
    for conv in convs:
        msgs = _messages(db, conv["id"], cutoff)
        if not msgs:
            continue
        msgs = [m for m in msgs if m["register"] in cfg.registers]
        if not cfg.include_chat_turns:
            msgs = [m for m in msgs if _origin(m.get("meta")) != "chat"]
        if not any(m["is_me"] for m in msgs):
            continue
        holdout = _holdout(conv["id"], cfg.holdout_fraction, cfg.seed)
        standalone = conv["source_kind"] in ("git", "doc", "docs", "capture") or all(
            m["is_me"] for m in msgs
        )
        turns = build_turns(msgs, is_group=bool(conv["is_group"]))
        register_of = {}
        for m in msgs:
            register_of.setdefault(m["ts"], m["register"])
        per_conv = 0
        for i, turn in enumerate(turns):
            if turn.role != "assistant":
                continue
            wc = word_count(turn.text)
            if wc < cfg.min_target_words:
                stats.skip("target_too_short")
                continue
            if wc > cfg.max_target_words:
                stats.skip("target_too_long")
                continue
            if low_signal_reason(turn.text):
                stats.skip("low_signal")
                continue
            register = register_of.get(turn.ts, msgs[0]["register"])
            lang = next((m["lang"] for m in msgs if m["ts"] == turn.ts), None)
            context = [t for t in turns[max(0, i - cfg.context_window) : i]]
            context = [
                t for t in context if parse_iso(turn.ts) - parse_iso(t.ts) <= CONTEXT_MAX_AGE
            ]
            context = _fit_context(context, budget - len(turn.text))
            if standalone or not context or context[-1].role != "user":
                if not standalone and not cfg.include_openers:
                    stats.skip("no_context")
                    continue
                user_text = standalone_instruction(register, conv.get("title"))
                if context:
                    prior = "\n".join(t.text for t in context)
                    user_text = f"{prior}\n\n{user_text}"
                chat_messages = [
                    {
                        "role": "system",
                        "content": system_prompt(
                            name, register=register, lang=lang, setting=_setting(conv, [])
                        ),
                    },
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": turn.text},
                ]
            else:
                names = [n for t in context for n in t.names]
                chat_messages = [
                    {
                        "role": "system",
                        "content": system_prompt(
                            name, register=register, lang=lang, setting=_setting(conv, names)
                        ),
                    }
                ]
                # Alternate strictly; merge if the window starts on an assistant turn.
                for t in context:
                    if chat_messages[-1]["role"] == t.role:
                        chat_messages[-1]["content"] += "\n" + t.text
                    else:
                        chat_messages.append({"role": t.role, "content": t.text})
                if chat_messages[-1]["role"] != "user":
                    stats.skip("no_context")
                    continue
                chat_messages.append({"role": "assistant", "content": turn.text})
            key = hashlib.sha256(
                (chat_messages[-2]["content"] + "\x00" + turn.text).encode("utf-8")
            ).hexdigest()
            if key in seen:
                stats.skip("duplicate")
                continue
            seen.add(key)
            per_conv += 1
            if per_conv > cfg.max_per_conversation:
                stats.skip("conversation_cap")
                continue
            ex = Example(
                messages=chat_messages,
                meta={
                    "conversation_id": conv["id"],
                    "register": register,
                    "lang": lang,
                    "ts": turn.ts,
                    "n_context": len(context),
                    "source": conv["source_kind"],
                },
            )
            if holdout:
                stats.n_holdout += 1
            else:
                stats.n_examples += 1
                stats.n_words += wc
                stats.by_register[register] = stats.by_register.get(register, 0) + 1
                stats.by_lang[lang or "unknown"] = stats.by_lang.get(lang or "unknown", 0) + 1
            if stats.data_cutoff is None or turn.ts > stats.data_cutoff:
                stats.data_cutoff = turn.ts
            yield ex, holdout
        if per_conv:
            stats.conversations += 1


def build_snapshot(
    db: Database,
    settings: Settings,
    version: str,
    *,
    cutoff: str | None = None,
    out_dir: Path | None = None,
) -> tuple[Path, Manifest]:
    """Write train.jsonl / valid.jsonl / manifest.json for a version."""
    out = out_dir or (datasets_dir() / version)
    out.mkdir(parents=True, exist_ok=True)
    train_path = out / "train.jsonl"
    valid_path = out / "valid.jsonl"
    stats = DatasetStats()
    rng = random.Random(settings.train.seed)
    train_lines: list[str] = []
    valid_lines: list[str] = []
    for ex, holdout in iter_examples(db, settings, cutoff=cutoff, stats=stats):
        (valid_lines if holdout else train_lines).append(ex.to_json())
    rng.shuffle(train_lines)
    train_path.write_text("\n".join(train_lines) + ("\n" if train_lines else ""), encoding="utf-8")
    valid_path.write_text("\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8")
    # mlx-lm wants a test split too; a copy of valid is fine.
    (out / "test.jsonl").write_text(valid_path.read_text(encoding="utf-8"), encoding="utf-8")
    sha = hashlib.sha256(train_path.read_bytes()).hexdigest()
    manifest = Manifest(
        version=version,
        created_at=now_iso(),
        base_model=settings.train.base_model,
        data_cutoff=stats.data_cutoff,
        n_examples=stats.n_examples,
        n_holdout=stats.n_holdout,
        n_words=stats.n_words,
        by_register=stats.by_register,
        by_lang=stats.by_lang,
        conversations=stats.conversations,
        skipped=stats.skipped,
        train_sha256=sha,
        config=settings.train.model_dump(mode="json"),
    )
    (out / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out, manifest


def preview(db: Database, settings: Settings, n: int = 5) -> dict[str, Any]:
    stats = DatasetStats()
    samples: list[dict[str, Any]] = []
    rng = random.Random(settings.train.seed)
    for ex, holdout in iter_examples(db, settings, stats=stats):
        if holdout:
            continue
        # reservoir sample
        if len(samples) < n:
            samples.append(_sample_view(ex))
        else:
            j = rng.randrange(stats.n_examples)
            if j < n:
                samples[j] = _sample_view(ex)
    return {
        "n_examples": stats.n_examples,
        "n_holdout": stats.n_holdout,
        "n_words": stats.n_words,
        "by_register": stats.by_register,
        "by_lang": stats.by_lang,
        "conversations": stats.conversations,
        "skipped": stats.skipped,
        "data_cutoff": stats.data_cutoff,
        "samples": samples,
    }


def _sample_view(ex: Example) -> dict[str, Any]:
    return {
        "system": ex.messages[0]["content"],
        "context": [{"role": m["role"], "text": m["content"]} for m in ex.messages[1:-1]],
        "target": ex.messages[-1]["content"],
        "meta": ex.meta,
    }


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
