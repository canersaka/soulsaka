"""Pick and assemble the reference clip that zero-shot TTS clones from.

Zero-shot models want 6-12 s of clean speech with its exact transcript. The best
candidates are push-to-talk clips (guaranteed me) and listener clips that scored
highly against the voice profile. Several short clips are concatenated with a short
pause and their transcripts joined, which the models handle well.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from soulsaka.db import Database
from soulsaka.ml.audio import read_wav_mono16k, write_wav16k
from soulsaka.paths import data_dir

log = logging.getLogger(__name__)

REF_CLIP_KEY = "tts.reference_clip"
REF_TEXT_KEY = "tts.reference_text"
TARGET_SECONDS = (6.0, 12.0)


@dataclass
class Candidate:
    uid: str
    path: Path
    text: str
    duration_s: float
    score: float


def candidates(
    db: Database, root: Path, *, min_s: float = 1.5, max_s: float = 15.0
) -> list[Candidate]:
    rows = db.all(
        """SELECT uid, audio_path, text, duration_s, origin, speaker_score, speaker_is_me
           FROM captures WHERE status = 'done' AND audio_path IS NOT NULL AND text IS NOT NULL
             AND duration_s BETWEEN ? AND ? AND (speaker_is_me = 1 OR (speaker_is_me IS NULL AND origin = 'manual'))
           ORDER BY COALESCE(speaker_score, 0.5) DESC, duration_s DESC LIMIT 200""",
        (min_s, max_s),
    )
    out = []
    for r in rows:
        p = root / r["audio_path"]
        if not p.exists() or len((r["text"] or "").split()) < 3:
            continue
        score = (
            r["speaker_score"]
            if r["speaker_score"] is not None
            else (0.9 if r["origin"] == "manual" else 0.5)
        )
        out.append(Candidate(r["uid"], p, r["text"].strip(), float(r["duration_s"]), float(score)))
    return out


def assemble(
    cands: list[Candidate],
    out_path: Path,
    *,
    target: tuple[float, float] = TARGET_SECONDS,
    gap_s: float = 0.3,
) -> tuple[Path, str, list[Candidate]]:
    """Concatenate the best clips until the target length is reached."""
    chosen: list[Candidate] = []
    total = 0.0
    for c in cands:
        if total >= target[0] and total + c.duration_s > target[1]:
            continue
        chosen.append(c)
        total += c.duration_s + gap_s
        if total >= target[1]:
            break
    if not chosen:
        raise RuntimeError("no usable clips yet; record a few push-to-talk notes first")
    gap = np.zeros(int(16000 * gap_s), dtype=np.float32)
    parts: list[np.ndarray] = []
    for c in chosen:
        parts.append(read_wav_mono16k(c.path))
        parts.append(gap)
    write_wav16k(out_path, np.concatenate(parts[:-1]))
    text = " ".join(c.text.rstrip(".!?") + "." for c in chosen)
    return out_path, text, chosen


def set_reference(db: Database, rel_path: str, text: str) -> None:
    db.set_setting(REF_CLIP_KEY, rel_path)
    db.set_setting(REF_TEXT_KEY, text)


def get_reference(db: Database) -> tuple[str | None, str | None]:
    return db.get_setting(REF_CLIP_KEY), db.get_setting(REF_TEXT_KEY)


def build_reference(db: Database, *, root: Path | None = None) -> dict:
    root = root or data_dir()
    cands = candidates(db, root)
    out = root / "voice" / "reference.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    path, text, chosen = assemble(cands, out)
    rel = str(path.relative_to(root))
    set_reference(db, rel, text)
    return {
        "reference_clip": rel,
        "reference_text": text,
        "seconds": round(sum(c.duration_s for c in chosen) + 0.3 * (len(chosen) - 1), 2),
        "clips": [c.uid for c in chosen],
    }
