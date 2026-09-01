"""Speech to text. faster-whisper (CUDA/CPU), mlx-whisper (Apple Silicon), or a fake."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from soulsaka.config import ASRConfig, detect_accelerator
from soulsaka.ml.audio import wav_duration
from soulsaka.paths import models_dir

log = logging.getLogger(__name__)


@dataclass
class ASRResult:
    text: str
    language: str | None = None
    duration_s: float | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)


class ASR(Protocol):
    name: str

    def transcribe(self, path: Path, language: str | None = None) -> ASRResult: ...


class FakeASR:
    """Returns the contents of ``<audio>.txt`` if present, else a fixed string."""

    name = "fake"

    def __init__(self, default_text: str = "", language: str = "en"):
        self.default_text = default_text
        self.language = language

    def transcribe(self, path: Path, language: str | None = None) -> ASRResult:
        sidecar = Path(path).with_suffix(".txt")
        text = (
            sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else self.default_text
        )
        return ASRResult(
            text=text, language=language or self.language, duration_s=wav_duration(Path(path))
        )


class FasterWhisperASR:
    name = "faster-whisper"

    def __init__(self, cfg: ASRConfig, accelerator: str = "auto"):
        from faster_whisper import WhisperModel  # type: ignore

        device = detect_accelerator(cfg.device if cfg.device != "auto" else accelerator)
        if device == "mps":  # CTranslate2 has no Metal backend; CPU int8 is fine on M1 Pro.
            device = "cpu"
        compute = cfg.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        log.info("loading faster-whisper %s on %s (%s)", cfg.model, device, compute)
        self.model = WhisperModel(
            cfg.model,
            device=device,
            compute_type=compute,
            download_root=str(models_dir() / "whisper"),
        )
        self.min_no_speech_prob = cfg.min_no_speech_prob

    def transcribe(self, path: Path, language: str | None = None) -> ASRResult:
        segments, info = self.model.transcribe(
            str(path),
            language=language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        texts: list[str] = []
        segs: list[dict[str, Any]] = []
        for s in segments:
            if s.no_speech_prob is not None and s.no_speech_prob > self.min_no_speech_prob:
                continue
            t = s.text.strip()
            if not t:
                continue
            texts.append(t)
            segs.append(
                {"start": s.start, "end": s.end, "text": t, "no_speech_prob": s.no_speech_prob}
            )
        return ASRResult(
            text=" ".join(texts), language=info.language, duration_s=info.duration, segments=segs
        )


class MlxWhisperASR:
    name = "mlx-whisper"

    def __init__(self, cfg: ASRConfig):
        import mlx_whisper  # type: ignore  # noqa: F401

        self.repo = cfg.model if "/" in cfg.model else f"mlx-community/whisper-{cfg.model}"
        log.info("using mlx-whisper %s", self.repo)

    def transcribe(self, path: Path, language: str | None = None) -> ASRResult:
        import mlx_whisper  # type: ignore

        out = mlx_whisper.transcribe(str(path), path_or_hf_repo=self.repo, language=language)
        return ASRResult(
            text=(out.get("text") or "").strip(),
            language=out.get("language"),
            duration_s=wav_duration(Path(path)),
            segments=[
                {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "").strip()}
                for s in out.get("segments", [])
            ],
        )


def build_asr(cfg: ASRConfig, accelerator: str = "auto") -> ASR:
    if cfg.backend == "fake":
        return FakeASR()
    if cfg.backend == "mlx-whisper":
        return MlxWhisperASR(cfg)
    return FasterWhisperASR(cfg, accelerator)
