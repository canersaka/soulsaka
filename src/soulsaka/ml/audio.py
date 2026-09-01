"""Audio file helpers: duration, conversion to 16 kHz mono WAV."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

TARGET_SR = 16000


def wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            return frames / float(rate) if rate else None
    except Exception:
        pass
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(path))
        return float(info.duration)
    except Exception:
        return None


def read_wav_mono16k(path: Path) -> np.ndarray:
    """Load any supported audio as float32 mono at 16 kHz."""
    try:
        import soundfile as sf  # type: ignore

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
    except Exception:
        with wave.open(str(path), "rb") as w:
            sr = w.getframerate()
            n = w.getnchannels()
            raw = w.readframes(w.getnframes())
            width = w.getsampwidth()
        dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
        pcm = np.frombuffer(raw, dtype=dtype).astype(np.float32) / float(2 ** (8 * width - 1))
        mono = pcm.reshape(-1, n).mean(axis=1) if n > 1 else pcm
    if sr != TARGET_SR:
        mono = resample_linear(mono, sr, TARGET_SR)
    return mono.astype(np.float32)


def resample_linear(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or x.size == 0:
        return x
    n_out = int(round(x.size * sr_out / sr_in))
    xp = np.linspace(0.0, 1.0, num=x.size, endpoint=False)
    xq = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(xq, xp, x).astype(np.float32)


def write_wav16k(path: Path, samples: np.ndarray, sr: int = TARGET_SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def to_wav16k(src: Path, dst: Path) -> bool:
    """Convert any audio (webm/ogg/m4a/mp3/wav) into 16 kHz mono PCM WAV.

    Tries soundfile, then PyAV, then the ffmpeg binary. Returns False if none could
    decode the input; the caller then keeps the original file.
    """
    try:
        samples = read_wav_mono16k(src)
        write_wav16k(dst, samples)
        return True
    except Exception:
        pass
    try:
        import av  # type: ignore

        container = av.open(str(src))
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SR)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        container.close()
        if chunks:
            pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
            write_wav16k(dst, pcm)
            return True
    except Exception:
        pass
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        dst.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-ac",
                "1",
                "-ar",
                str(TARGET_SR),
                "-f",
                "wav",
                str(dst),
            ],
            capture_output=True,
        )
        if proc.returncode == 0 and dst.exists():
            return True
    return False
