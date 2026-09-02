"""The capture loop: frames from a source -> VAD -> segmenter -> spool (-> uploader)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from soulsaka.listener.audio_input import FrameSource
from soulsaka.listener.segmenter import Segment, Segmenter
from soulsaka.listener.spool import Spool, SpoolEntry
from soulsaka.listener.uploader import Uploader
from soulsaka.listener.vad import VAD, frame_dbfs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListenerStatus:
    state: str = "starting"  # starting | listening | speech | stopped
    level_db: float = -100.0
    prob: float = 0.0
    frames: int = 0
    segments: int = 0
    dropped: int = 0
    last_segment_s: float | None = None
    error: str | None = None


class Listener:
    """Runs the pipeline for one source. Thread-safe status snapshot for a display."""

    def __init__(
        self,
        source: FrameSource,
        vad: VAD,
        segmenter: Segmenter,
        spool: Spool,
        uploader: Uploader | None = None,
        *,
        on_segment: Callable[[Segment, SpoolEntry], None] | None = None,
    ) -> None:
        self.source = source
        self.vad = vad
        self.segmenter = segmenter
        self.spool = spool
        self.uploader = uploader
        self.on_segment = on_segment
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._status = ListenerStatus()

    def status(self) -> ListenerStatus:
        with self._lock:
            return self._status

    def run(self, stop: threading.Event) -> None:
        """Consume the source until it ends or ``stop`` is set; flushes the open segment."""
        self._update(state="listening")
        frames = 0
        try:
            for frame, at in self.source.frames(stop):
                prob = self.vad.prob(frame)
                was_open = self.segmenter.in_speech
                segment = self.segmenter.feed(frame, prob, at)
                if segment is not None:
                    self._emit(segment)
                if was_open and not self.segmenter.in_speech:
                    self.vad.reset()
                frames += 1
                self._update(
                    state="speech" if self.segmenter.in_speech else "listening",
                    level_db=frame_dbfs(frame),
                    prob=prob,
                    frames=frames,
                )
                if stop.is_set():
                    break
            segment = self.segmenter.flush()
            if segment is not None:
                self._emit(segment)
        except Exception as e:  # noqa: BLE001 - surfaced to the CLI, which exits non-zero
            log.exception("listener stopped on an error")
            self._update(error=f"{type(e).__name__}: {e}")
        finally:
            self._update(state="stopped")
            self.finished.set()

    def _emit(self, segment: Segment) -> None:
        entry = self.spool.write(segment)
        log.info("segment %s: %.2fs starting %s", entry.uid, segment.duration_s, entry.client_ts)
        with self._lock:
            self._status = replace(
                self._status,
                segments=self._status.segments + 1,
                last_segment_s=segment.duration_s,
            )
        if self.uploader is not None:
            self.uploader.wake()
        if self.on_segment is not None:
            self.on_segment(segment, entry)

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._status = replace(self._status, dropped=self.segmenter.dropped, **changes)  # type: ignore[arg-type]
