"""Export verified clips of my voice as a TTS fine-tuning dataset.

Layout is the LJSpeech-style one F5-TTS's ``prepare_csv_wavs`` expects:

    <out>/wavs/<uid>.wav
    <out>/metadata.csv        audio_file|text   (pipe separated, no header)
    <out>/manifest.json       counts, total seconds, cutoff
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from soulsaka.db import Database
from soulsaka.text.normalize import word_count
from soulsaka.util.time import now_iso


def export_tts_dataset(
    db: Database,
    root: Path,
    out_dir: Path,
    *,
    min_s: float = 1.0,
    max_s: float = 20.0,
    min_words: int = 2,
) -> dict[str, Any]:
    rows = db.all(
        """SELECT uid, audio_path, text, duration_s, speaker_score, origin FROM captures
           WHERE status = 'done' AND audio_path IS NOT NULL AND text IS NOT NULL
             AND duration_s BETWEEN ? AND ?
             AND (speaker_is_me = 1 OR (speaker_is_me IS NULL AND origin = 'manual'))
           ORDER BY id""",
        (min_s, max_s),
    )
    wavs = out_dir / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    seconds = 0.0
    skipped = 0
    for r in rows:
        src = root / r["audio_path"]
        text = " ".join((r["text"] or "").split()).replace("|", "/")
        if not src.exists() or word_count(text) < min_words:
            skipped += 1
            continue
        dst = wavs / f"{r['uid']}.wav"
        if not dst.exists():
            shutil.copyfile(src, dst)
        lines.append(f"{dst.name}|{text}")
        seconds += float(r["duration_s"] or 0.0)
    (out_dir / "metadata.csv").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    manifest = {
        "created_at": now_iso(),
        "clips": len(lines),
        "seconds": round(seconds, 1),
        "hours": round(seconds / 3600, 2),
        "skipped": skipped,
        "ready_for_finetune": seconds >= 3600,
        "format": "ljspeech (audio_file|text)",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
