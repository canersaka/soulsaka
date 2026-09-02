"""Turn VAD-scored frames into speech segments.

A pure-numpy state machine with no I/O, so it is fully unit-testable:

* **idle** keeps a pre-roll ring buffer of ``pad_s`` so the first syllable is not clipped;
* a frame whose probability exceeds ``threshold`` opens a *candidate* segment;
* the candidate is confirmed once ``min_speech_s`` of speech frames has accumulated,
  otherwise it is dropped as a blip when the silence runs out;
* a segment ends after ``silence_end_s`` of silence, keeping ``pad_s`` of that tail;
* a segment that reaches ``max_segment_s`` is emitted and a new one continues seamlessly
  (no pre-roll, so nothing is duplicated).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from soulsaka.util.time import utcnow

SAMPLE_RATE = 16000
FRAME_SIZE = 512


@dataclass(frozen=True)
class Segment:
    """One stretch of speech: float32 mono samples at ``SAMPLE_RATE``."""

    samples: np.ndarray
    started_at: datetime
    duration_s: float


class Segmenter:
    """Feed 512-sample frames with their VAD probability; get :class:`Segment` objects back."""

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        frame_size: int = FRAME_SIZE,
        threshold: float = 0.5,
        min_speech_s: float = 0.6,
        silence_end_s: float = 0.8,
        max_segment_s: float = 30.0,
        pad_s: float = 0.25,
    ) -> None:
        if sample_rate <= 0 or frame_size <= 0:
            raise ValueError("sample_rate and frame_size must be positive")
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.threshold = threshold
        self.frame_s = frame_size / sample_rate
        self._min_speech_frames = max(1, math.ceil(min_speech_s / self.frame_s))
        self._silence_frames = max(1, math.ceil(silence_end_s / self.frame_s))
        self._max_frames = max(self._min_speech_frames, math.ceil(max_segment_s / self.frame_s))
        self._pad_frames = max(0, int(round(pad_s / self.frame_s)))
        # The ring holds the pre-roll plus the frame that triggers a segment.
        self._ring: deque[np.ndarray] = deque(maxlen=self._pad_frames + 1)
        self._buf: list[np.ndarray] | None = None
        self._started_at: datetime | None = None
        self._speech_frames = 0
        self._silence_run = 0
        self.emitted = 0
        self.dropped = 0

    # -- state -------------------------------------------------------------------------
    @property
    def in_speech(self) -> bool:
        """True while a candidate or confirmed segment is open."""
        return self._buf is not None

    @property
    def confirmed(self) -> bool:
        """True once the open segment has enough speech to be emitted."""
        return self._buf is not None and self._speech_frames >= self._min_speech_frames

    @property
    def open_duration_s(self) -> float:
        return (len(self._buf) * self.frame_s) if self._buf else 0.0

    # -- feeding -----------------------------------------------------------------------
    def feed(self, frame: np.ndarray, prob: float, at: datetime | None = None) -> Segment | None:
        """Consume one frame. Returns a finished segment when one ends on this frame.

        ``at`` is the wall-clock time of the frame (defaults to now); it becomes the
        ``started_at`` of a segment, minus the pre-roll.
        """
        if frame.ndim != 1 or frame.shape[0] != self.frame_size:
            raise ValueError(f"expected a frame of {self.frame_size} samples, got {frame.shape}")
        at = at if at is not None else utcnow()
        speech = prob > self.threshold
        self._ring.append(frame)
        if self._buf is None:
            if not speech:
                return None
            self._buf = list(self._ring)
            self._started_at = at - timedelta(seconds=(len(self._buf) - 1) * self.frame_s)
            self._speech_frames = 1
            self._silence_run = 0
            return self._maybe_split(at)
        self._buf.append(frame)
        if speech:
            self._speech_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self._silence_frames:
                return self._finish(trim_to_pad=True)
        return self._maybe_split(at)

    def flush(self) -> Segment | None:
        """Close the open segment (shutdown, end of file). Blips are still dropped."""
        if self._buf is None:
            return None
        return self._finish(trim_to_pad=True)

    # -- internals ---------------------------------------------------------------------
    def _maybe_split(self, at: datetime) -> Segment | None:
        if self._buf is None or len(self._buf) < self._max_frames:
            return None
        seg = self._finish(trim_to_pad=False)
        # Keep going without pre-roll: the next frame continues the same utterance.
        self._buf = []
        self._started_at = at + timedelta(seconds=self.frame_s)
        self._speech_frames = 0
        return seg

    def _finish(self, *, trim_to_pad: bool) -> Segment | None:
        buf = self._buf or []
        speech_frames = self._speech_frames
        started_at = self._started_at or utcnow()
        self._buf = None
        self._started_at = None
        self._speech_frames = 0
        if trim_to_pad and self._silence_run > self._pad_frames:
            keep = max(0, len(buf) - (self._silence_run - self._pad_frames))
            buf = buf[:keep]
        if not buf:
            return None
        if speech_frames < self._min_speech_frames:
            self.dropped += 1
            return None
        samples = np.concatenate(buf).astype(np.float32, copy=False)
        self.emitted += 1
        return Segment(
            samples=samples,
            started_at=started_at,
            duration_s=samples.shape[0] / self.sample_rate,
        )
