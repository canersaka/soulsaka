"""Frame sources: the microphone (via ``sounddevice``) and audio files.

Both yield ``(frame, at)`` pairs where ``frame`` is 512 float32 samples at 16 kHz and ``at``
is the wall-clock time of the frame. ``sounddevice`` (PortAudio) is imported only inside
the function that opens the stream, so everything else works without it.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from soulsaka.ml.audio import read_wav_mono16k, resample_linear
from soulsaka.util.time import utcnow

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SIZE = 512

Frame = tuple[np.ndarray, datetime]


class FrameSource(Protocol):
    name: str

    def frames(self, stop: threading.Event) -> Iterator[Frame]:
        """Yield frames until exhausted or ``stop`` is set."""
        ...


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool


def list_input_devices() -> list[DeviceInfo]:
    """Input devices known to PortAudio. Raises ImportError/OSError without sounddevice."""
    import sounddevice as sd  # type: ignore[import-not-found]

    try:
        default_in = int(sd.default.device[0])
    except Exception:  # noqa: BLE001 - no default input device
        default_in = -1
    out: list[DeviceInfo] = []
    for index, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        out.append(
            DeviceInfo(
                index=index,
                name=str(dev.get("name", f"device {index}")),
                channels=int(dev["max_input_channels"]),
                default_samplerate=float(dev.get("default_samplerate") or 0.0),
                is_default=index == default_in,
            )
        )
    return out


def resolve_device(spec: str | int | None, devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Pick a device by index, exact name, or case-insensitive substring. None = default."""
    if spec is None or (isinstance(spec, str) and not spec.strip()):
        return None
    if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().isdigit()):
        index = int(spec)
        for d in devices:
            if d.index == index:
                return d
        raise ValueError(f"no input device with index {index}")
    needle = str(spec).strip().lower()
    for d in devices:
        if d.name.lower() == needle:
            return d
    matches = [d for d in devices if needle in d.name.lower()]
    if not matches:
        raise ValueError(f"no input device matching {spec!r}")
    return matches[0]


class MicSource:
    """Frames from the microphone.

    Opens a 16 kHz mono float32 stream with a 512-sample block size. If the host API refuses
    16 kHz (some Windows/WASAPI devices), the device's native rate is used and the audio is
    resampled here.
    """

    def __init__(
        self,
        device: str | int | None = None,
        *,
        sample_rate: int = SAMPLE_RATE,
        frame_size: int = FRAME_SIZE,
        queue_seconds: float = 30.0,
    ) -> None:
        self.device_spec = device
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.name = "default input"
        self.overflows = 0
        self.native_rate = sample_rate
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=max(1, int(queue_seconds * sample_rate / frame_size))
        )
        self._acc = np.zeros(0, dtype=np.float32)

    def frames(self, stop: threading.Event) -> Iterator[Frame]:
        import sounddevice as sd  # type: ignore[import-not-found]

        devices = list_input_devices()
        chosen = resolve_device(self.device_spec, devices)
        index: int | None = chosen.index if chosen else None
        default = next((d for d in devices if d.is_default), None)
        self.name = (chosen or default).name if (chosen or default) else "default input"
        stream = self._open(sd, index, chosen or default)
        log.info("listening on %r at %d Hz", self.name, self.native_rate)
        with stream:
            while not stop.is_set():
                try:
                    block = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                at = utcnow()
                for frame in self._to_frames(block):
                    yield frame, at

    def _open(self, sd: Any, index: int | None, info: DeviceInfo | None) -> Any:
        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status and getattr(status, "input_overflow", False):
                self.overflows += 1
            try:
                self._queue.put_nowait(np.asarray(indata[:, 0], dtype=np.float32).copy())
            except queue.Full:
                self.overflows += 1

        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.frame_size,
                device=index,
                latency="low",
                callback=callback,
            )
            self.native_rate = self.sample_rate
            return stream
        except sd.PortAudioError as e:
            native = int(info.default_samplerate) if info and info.default_samplerate else 0
            if native <= 0 or native == self.sample_rate:
                raise
            log.warning(
                "%s at %d Hz (%s); falling back to %d Hz with resampling",
                self.name,
                self.sample_rate,
                e,
                native,
            )
        self.native_rate = native
        return sd.InputStream(
            samplerate=native,
            channels=1,
            dtype="float32",
            blocksize=int(round(self.frame_size * native / self.sample_rate)),
            device=index,
            latency="low",
            callback=callback,
        )

    def _to_frames(self, block: np.ndarray) -> list[np.ndarray]:
        if self.native_rate != self.sample_rate:
            block = resample_linear(block, self.native_rate, self.sample_rate)
        self._acc = np.concatenate([self._acc, block]) if self._acc.size else block
        n = self.frame_size
        count = self._acc.shape[0] // n
        frames = [self._acc[i * n : (i + 1) * n] for i in range(count)]
        self._acc = self._acc[count * n :]
        return frames


class FileSource:
    """Frames from an audio file, as fast as possible (tests, offline runs)."""

    def __init__(
        self, path: Path, *, frame_size: int = FRAME_SIZE, start: datetime | None = None
    ) -> None:
        self.path = Path(path)
        self.name = self.path.name
        self.frame_size = frame_size
        self._start = start

    def frames(self, stop: threading.Event) -> Iterator[Frame]:
        samples = read_wav_mono16k(self.path)
        n = self.frame_size
        pad = (-samples.shape[0]) % n
        if pad:
            samples = np.pad(samples, (0, pad))
        base = self._start or utcnow()
        for i in range(0, samples.shape[0], n):
            if stop.is_set():
                return
            yield samples[i : i + n], base + timedelta(seconds=i / SAMPLE_RATE)
