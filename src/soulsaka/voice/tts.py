"""Text to speech in my voice. Zero-shot cloning from a reference clip on day one;
fine-tuned checkpoints plug in behind the same interface later."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from soulsaka.config import TTSConfig
from soulsaka.ml.audio import write_wav16k

log = logging.getLogger(__name__)


class TTS(Protocol):
    name: str

    def synthesize(self, text: str, out_path: Path) -> Path: ...


class FakeTTS:
    name = "fake"

    def synthesize(self, text: str, out_path: Path) -> Path:
        # A short, quiet tone whose length scales with the text: enough to exercise the plumbing.
        seconds = max(0.3, min(6.0, 0.06 * len(text)))
        t = np.arange(int(16000 * seconds)) / 16000.0
        write_wav16k(out_path, (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
        return out_path


class F5TTS:
    name = "f5-tts"

    def __init__(self, reference_clip: Path, reference_text: str | None):
        from f5_tts.api import F5TTS as _F5  # type: ignore

        self.model = _F5()
        self.ref = str(reference_clip)
        self.ref_text = reference_text or ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        import soundfile as sf  # type: ignore

        wav, sr, _ = self.model.infer(ref_file=self.ref, ref_text=self.ref_text, gen_text=text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), wav, sr)
        return out_path


def build_tts(cfg: TTSConfig, state) -> TTS:
    if cfg.backend == "fake":
        return FakeTTS()
    from soulsaka.voice.reference import get_reference

    ref, ref_text = cfg.reference_clip, cfg.reference_text
    if not ref:
        ref, ref_text = get_reference(state.db)
    if not ref:
        raise RuntimeError(
            "no reference clip yet; run `soulsaka voice reference` after recording a few notes"
        )
    ref_path = Path(ref)
    if not ref_path.is_absolute():
        ref_path = state.abs_path(ref)
    if cfg.backend == "f5-tts":
        return F5TTS(ref_path, ref_text)
    raise RuntimeError(f"tts backend {cfg.backend!r} not implemented yet")
