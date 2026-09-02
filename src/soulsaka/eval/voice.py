"""How much the cloned voice sounds like me: speaker-embedding cosine similarity
between synthesised speech and the enrolled voice profile, with a real-clip baseline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from soulsaka.hub.state import HubState
from soulsaka.paths import evals_dir
from soulsaka.text.normalize import word_count
from soulsaka.util.time import now_iso

log = logging.getLogger(__name__)


def _sentences(state: HubState, n: int) -> list[str]:
    rows = state.db.all(
        "SELECT text FROM messages WHERE is_me = 1 AND register IN ('speech', 'text') ORDER BY RANDOM() LIMIT ?",
        (n * 3,),
    )
    out = [r[0] for r in rows if 4 <= word_count(r[0]) <= 25]
    return out[:n] or ["This is a short test sentence to check the voice."]


def run_voice_similarity(state: HubState, version: str, *, n: int = 8) -> dict[str, Any]:
    speaker = state.service("speaker")
    prof = speaker.load_profile(state.db)
    if prof is None:
        raise RuntimeError("no enrolled voice profile; record a few push-to-talk clips first")
    centroid, _ = prof
    tts = state.service("tts")
    out_dir = evals_dir() / version / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    scores: list[float] = []
    for i, sentence in enumerate(_sentences(state, n)):
        wav = tts.synthesize(sentence, out_dir / f"{i:02d}.wav")
        emb = speaker.backend.embed(Path(wav))
        scores.append(float(np.clip(np.dot(emb, centroid), -1.0, 1.0)))
    # Baseline: real clips of me against the same centroid.
    baseline: list[float] = []
    for r in state.db.all(
        "SELECT audio_path FROM captures WHERE speaker_is_me = 1 AND audio_path IS NOT NULL ORDER BY RANDOM() LIMIT ?",
        (n,),
    ):
        p = state.abs_path(r[0])
        if p.exists():
            baseline.append(float(np.clip(np.dot(speaker.backend.embed(p), centroid), -1.0, 1.0)))
    value = float(np.mean(scores)) if scores else float("nan")
    details = {
        "scores": scores,
        "baseline_real": baseline,
        "baseline_mean": float(np.mean(baseline)) if baseline else None,
        "tts": getattr(tts, "name", "?"),
    }
    with state.db.tx() as conn:
        conn.execute(
            "DELETE FROM eval_results WHERE version = ? AND kind = 'voice_similarity'", (version,)
        )
        conn.execute(
            "INSERT INTO eval_results(version, kind, metric, value, n, details, created_at) VALUES (?, 'voice_similarity', 'cosine', ?, ?, ?, ?)",
            (version, value, len(scores), json.dumps(details), now_iso()),
        )
    state.events.publish("eval", version=version, kind="voice_similarity", cosine=value)
    return {"version": version, "cosine": value, **details}
