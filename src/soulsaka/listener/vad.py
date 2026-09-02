"""Voice activity detection on 512-sample frames of 16 kHz mono audio.

Two implementations share the :class:`VAD` interface (``prob(frame) -> float``):

* :class:`SileroVAD` wraps the ``silero_vad`` package: a small neural network, the most
  reliable option, about a millisecond per frame on a laptop CPU.
* :class:`EnergyVAD` is the dependency-free fallback: RMS level in dBFS compared against an
  adaptive noise floor.

:func:`make_vad` picks Silero when it can be imported and falls back to energy otherwise.
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections import deque
from typing import Literal, Protocol

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SIZE = 512  # 32 ms; silero needs exactly 512 samples per call at 16 kHz
SILENCE_DBFS = -100.0

VadKind = Literal["auto", "silero", "energy"]


class VAD(Protocol):
    """Per-frame speech probability in ``[0, 1]``."""

    name: str

    def prob(self, frame: np.ndarray) -> float:
        """Probability that ``frame`` (float32, ``FRAME_SIZE`` samples) contains speech."""
        ...

    def reset(self) -> None:
        """Forget per-segment state; called when a segment ends."""
        ...


def frame_dbfs(frame: np.ndarray, floor_db: float = SILENCE_DBFS) -> float:
    """RMS level of a frame in dBFS, clamped at ``floor_db`` for digital silence."""
    if frame.size == 0:
        return floor_db
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    if rms <= 0.0:
        return floor_db
    return max(floor_db, 20.0 * math.log10(rms))


class EnergyVAD:
    """Level-based VAD against an adaptive noise floor.

    The floor tracks the minimum frame level over the last ``window_s`` seconds, smoothed
    exponentially (fast when the level drops, slow when it rises), which behaves like a
    running low percentile of the level: it sits at the room noise and does not climb during
    speech because natural speech has gaps that fall back to the floor several times a
    second. A frame counts as speech when it is more than ``margin_db`` above the floor and
    above the absolute ``abs_floor_db`` gate. The probability is a sigmoid of the margin so
    that the default threshold of 0.5 corresponds exactly to ``floor + margin_db``.
    """

    name = "energy"

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        frame_size: int = FRAME_SIZE,
        margin_db: float = 12.0,
        abs_floor_db: float = -45.0,
        window_s: float = 8.0,
        softness_db: float = 3.0,
    ) -> None:
        frames = max(1, int(round(window_s * sample_rate / frame_size)))
        self.margin_db = margin_db
        self.abs_floor_db = abs_floor_db
        self.softness_db = max(softness_db, 1e-3)
        self._window: deque[float] = deque(maxlen=frames)
        self._floor: float | None = None
        self.level_db = SILENCE_DBFS

    @property
    def noise_floor_db(self) -> float | None:
        """Current estimate of the background level, or None before the first frame."""
        return self._floor

    def prob(self, frame: np.ndarray) -> float:
        db = frame_dbfs(frame)
        self.level_db = db
        self._window.append(db)
        window_min = min(self._window)
        if self._floor is None:
            self._floor = window_min
        elif window_min < self._floor:
            self._floor += (window_min - self._floor) * 0.5  # attack: follow drops quickly
        else:
            self._floor += (window_min - self._floor) * 0.05  # release: rise slowly
        if db < self.abs_floor_db:
            return 0.0
        margin = db - (self._floor + self.margin_db)
        return 1.0 / (1.0 + math.exp(-margin / self.softness_db))

    def reset(self) -> None:
        """No per-segment state: the noise floor belongs to the stream, not the segment."""
        return None


class SileroVAD:
    """The Silero neural VAD (``pip install silero-vad``; pulls in torch)."""

    name = "silero"

    def __init__(self, *, sample_rate: int = SAMPLE_RATE) -> None:
        import torch  # type: ignore[import-not-found]
        from silero_vad import load_silero_vad  # type: ignore[import-not-found]

        if sample_rate not in (8000, 16000):
            raise ValueError("silero VAD supports 8 kHz or 16 kHz only")
        self._torch = torch
        self._sample_rate = sample_rate
        with contextlib.suppress(Exception):  # purely an optimisation
            torch.set_num_threads(1)  # one 512-sample frame at a time; keep the CPU quiet
        self._model = load_silero_vad()

    def prob(self, frame: np.ndarray) -> float:
        x = self._torch.from_numpy(np.ascontiguousarray(frame, dtype=np.float32))
        with self._torch.no_grad():
            return float(self._model(x, self._sample_rate).item())

    def reset(self) -> None:
        self._model.reset_states()


def make_vad(kind: VadKind = "auto") -> VAD:
    """Build the requested detector; ``auto`` prefers Silero and falls back to energy."""
    if kind == "energy":
        return EnergyVAD()
    if kind not in ("auto", "silero"):
        raise ValueError(f"unknown VAD {kind!r}; expected auto, silero or energy")
    try:
        vad: VAD = SileroVAD()
    except ImportError as e:
        if kind == "silero":
            raise RuntimeError(
                "silero_vad is not installed; run `uv sync --extra listener` or use --vad energy"
            ) from e
        log.info("silero_vad not available (%s); using the energy VAD", e)
        return EnergyVAD()
    except Exception as e:  # noqa: BLE001 - torch can fail in many creative ways
        if kind == "silero":
            raise
        log.warning("silero_vad failed to load (%s: %s); using the energy VAD", type(e).__name__, e)
        return EnergyVAD()
    log.info("using the silero VAD")
    return vad
