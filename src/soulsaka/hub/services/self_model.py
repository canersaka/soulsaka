"""The self-model: a short document describing who I am and how I write.

Two layers, regenerated together:
  * a **style fingerprint** computed from the corpus (no model needed): message length,
    capitalisation, punctuation, emoji, favourite words, language mix, active hours;
  * a **narrative** written by the LLM from memories and a sample of my messages.
The result is `self_model.md` in the data directory and goes into every prompt.
"""

from __future__ import annotations

import contextlib
import logging
import random
import re
import shutil
from collections import Counter
from datetime import datetime
from typing import Any

from soulsaka.db import Database
from soulsaka.hub.state import HubState
from soulsaka.ml.llm import ChatMessage, LLMError
from soulsaka.paths import data_dir
from soulsaka.text.normalize import word_count
from soulsaka.util.time import now_iso

log = logging.getLogger(__name__)

SELF_MODEL_FILE = "self_model.md"
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_STOP = set(
    [
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
        "a",
        "an",
        "i",
        "its",
        "it's",
        "u",
        "ur",
        "ve",
        "bir",
        "bu",
        "ne",
        "ben",
        "sen",
        "de",
        "da",
        "ile",
        "ama",
        "çok",
        "için",
        "gibi",
        "daha",
        "var",
        "yok",
        "mi",
        "mı",
        "mu",
        "mü",
        "şey",
        "evet",
        "hayır",
        "tamam",
    ]
)


def style_stats(db: Database) -> dict[str, Any]:
    rows = db.all(
        "SELECT text, register, lang, ts FROM messages WHERE is_me = 1 ORDER BY ts DESC LIMIT 20000"
    )
    if not rows:
        return {"n": 0}
    texts = [r["text"] for r in rows if r["register"] == "text"] or [r["text"] for r in rows]
    n = len(texts)
    words = Counter()
    emojis = Counter()
    lower_start = ends_punct = has_emoji = multi_line = 0
    lengths = []
    for t in texts:
        lengths.append(word_count(t))
        first = next((ch for ch in t if ch.isalpha()), "")
        lower_start += 1 if first and first.islower() else 0
        ends_punct += 1 if t.rstrip()[-1:] in ".!?" else 0
        found = _EMOJI_RE.findall(t)
        has_emoji += 1 if found else 0
        emojis.update(found)
        multi_line += 1 if "\n" in t else 0
        for w in _WORD_RE.findall(t.casefold()):
            if w not in _STOP:
                words[w] += 1
    langs = Counter(r["lang"] or "unknown" for r in rows)
    hours = Counter()
    for r in rows:
        with contextlib.suppress(ValueError):
            hours[datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).hour] += 1
    lengths.sort()
    return {
        "n": n,
        "median_words": lengths[len(lengths) // 2],
        "mean_words": round(sum(lengths) / n, 1),
        "lowercase_start_pct": round(100 * lower_start / n),
        "ends_with_punctuation_pct": round(100 * ends_punct / n),
        "emoji_pct": round(100 * has_emoji / n),
        "multi_line_pct": round(100 * multi_line / n),
        "top_words": [w for w, _ in words.most_common(25)],
        "top_emojis": [e for e, _ in emojis.most_common(8)],
        "languages": {k: round(100 * v / len(rows)) for k, v in langs.most_common()},
        "peak_hours_utc": [h for h, _ in hours.most_common(3)],
        "registers": dict(Counter(r["register"] for r in rows)),
    }


def fingerprint_markdown(stats: dict[str, Any]) -> str:
    if not stats.get("n"):
        return "_No messages yet._"
    lines = [
        f"- Typical text message: {stats['median_words']} words (mean {stats['mean_words']}).",
        f"- Starts lowercase {stats['lowercase_start_pct']}% of the time; ends with punctuation {stats['ends_with_punctuation_pct']}%.",
        f"- Uses emoji in {stats['emoji_pct']}% of messages"
        + (f" (mostly {' '.join(stats['top_emojis'])})." if stats["top_emojis"] else "."),
        "- Languages: " + ", ".join(f"{k} {v}%" for k, v in stats["languages"].items()) + ".",
        f"- Favourite words: {', '.join(stats['top_words'][:15])}.",
    ]
    return "\n".join(lines)


NARRATIVE_SYSTEM = """You write a private profile of one person from evidence about them, for a system
that must imitate how they write. Be specific and concrete; quote short phrases they use.
Cover: who they are (facts), what they care about, how they write (tone, punctuation,
slang, sentence length, when they switch languages), how they argue and joke, and things
they would never say or do. Never invent; if evidence is thin, say less. Markdown, at most
350 words, no preamble."""


def _sample_messages(db: Database, k: int = 60) -> list[str]:
    rows = db.all(
        "SELECT text, register FROM messages WHERE is_me = 1 AND word_count BETWEEN 4 AND 60 ORDER BY ts DESC LIMIT 3000"
    )
    rng = random.Random(7)
    picked = rng.sample(rows, min(k, len(rows))) if rows else []
    return [f"[{r['register']}] {r['text']}" for r in picked]


def _memories(db: Database, k: int = 150) -> list[str]:
    rows = db.all(
        "SELECT kind, text FROM memories WHERE archived = 0 ORDER BY updated_at DESC LIMIT ?", (k,)
    )
    return [f"[{r['kind']}] {r['text']}" for r in rows]


def regenerate(state: HubState, *, use_llm: bool = True) -> str:
    """Rebuild self_model.md. Returns the document text."""
    name = state.settings.me.display_name or (
        state.settings.me.names[0] if state.settings.me.names else "me"
    )
    stats = style_stats(state.db)
    parts = [f"# {name}", "", "## Style fingerprint", fingerprint_markdown(stats), ""]
    narrative = ""
    if use_llm:
        llm = state.service("llm")
        if llm.available():
            evidence = "\n".join(
                [
                    "## Memories",
                    *_memories(state.db),
                    "",
                    "## Sample of their own messages",
                    *_sample_messages(state.db),
                ]
            )
            try:
                resp = llm.complete(
                    [
                        ChatMessage("system", NARRATIVE_SYSTEM),
                        ChatMessage("user", f"Person: {name}\n\n{evidence}"),
                    ],
                    temperature=0.3,
                    max_tokens=700,
                )
                narrative = resp.text.strip()
            except LLMError as e:
                log.warning("self-model narrative skipped: %s", e)
    if narrative:
        parts += ["## Profile", narrative, ""]
    parts.append(
        f"_Regenerated {now_iso()} from {stats.get('n', 0)} messages and {len(_memories(state.db))} memories._"
    )
    doc = "\n".join(parts)
    path = data_dir() / SELF_MODEL_FILE
    if path.exists():
        hist = data_dir() / "self_model_history"
        hist.mkdir(exist_ok=True)
        shutil.copy(path, hist / f"{now_iso()[:19].replace(':', '-')}.md")
    path.write_text(doc, encoding="utf-8")
    state.events.publish("self_model", chars=len(doc))
    return doc


def current(state: HubState) -> str:
    path = data_dir() / SELF_MODEL_FILE
    return path.read_text(encoding="utf-8") if path.exists() else ""
