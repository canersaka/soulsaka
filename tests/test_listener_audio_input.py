"""MicSource against a fake ``sounddevice`` module (no PortAudio needed), plus FileSource."""

from __future__ import annotations

import sys
import threading
import types
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from soulsaka.listener.audio_input import (
    FRAME_SIZE,
    SAMPLE_RATE,
    FileSource,
    MicSource,
    list_input_devices,
    resolve_device,
)
from soulsaka.ml.audio import write_wav16k

BLOCKS_PER_STREAM = 200  # the fake pumps this many callbacks, as fast as it can, then idles


class FakePortAudioError(Exception):
    pass


class FakeStream:
    """Feeds a 220 Hz tone to the callback from a thread, like PortAudio would."""

    def __init__(
        self, module, *, samplerate, channels, dtype, blocksize, device, latency, callback
    ):
        module.attempts.append(samplerate)
        if samplerate not in module.supported_rates:
            raise module.PortAudioError(f"Invalid sample rate {samplerate}")
        assert channels == 1 and dtype == "float32"
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.device = device
        self.callback = callback
        self._thread: threading.Thread | None = None
        module.streams.append(self)

    def __enter__(self):
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._thread.join(5.0)

    def _pump(self):
        pos = 0
        for _ in range(BLOCKS_PER_STREAM):
            t = (np.arange(self.blocksize) + pos) / self.samplerate
            block = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32).reshape(-1, 1)
            self.callback(block, self.blocksize, None, None)
            pos += self.blocksize


def fake_sounddevice(supported_rates=(16000, 48000)) -> types.ModuleType:
    mod = types.ModuleType("sounddevice")
    mod.PortAudioError = FakePortAudioError
    mod.supported_rates = set(supported_rates)
    mod.streams = []  # streams that opened
    mod.attempts = []  # sample rates requested, including refused ones
    mod.default = types.SimpleNamespace(device=[1, -1])
    mod.query_devices = lambda: [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
        {"name": "Fake Mic", "max_input_channels": 1, "default_samplerate": 48000.0},
        {"name": "USB Mic Pro", "max_input_channels": 2, "default_samplerate": 44100.0},
    ]
    mod.InputStream = lambda **kw: FakeStream(mod, **kw)
    return mod


def collect(source: MicSource, n: int):
    stop = threading.Event()
    frames = []
    for frame, at in source.frames(stop):
        frames.append((frame, at))
        if len(frames) >= n:
            stop.set()
    return frames


def dominant_hz(x: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(x))
    return float(np.fft.rfftfreq(x.shape[0], 1.0 / SAMPLE_RATE)[int(np.argmax(spectrum))])


def test_list_and_resolve_devices(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice())
    devices = list_input_devices()
    assert [d.index for d in devices] == [1, 2]
    assert [d.is_default for d in devices] == [True, False]
    assert resolve_device(None, devices) is None and resolve_device("  ", devices) is None
    assert resolve_device("usb", devices).index == 2
    assert resolve_device("Fake Mic", devices).index == 1
    assert resolve_device("2", devices).index == 2 and resolve_device(1, devices).index == 1
    with pytest.raises(ValueError):
        resolve_device("nope", devices)
    with pytest.raises(ValueError):
        resolve_device("9", devices)


def test_mic_source_yields_16k_frames(monkeypatch):
    sd = fake_sounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    source = MicSource("fake mic")
    frames = collect(source, 40)
    assert source.name == "Fake Mic" and source.native_rate == SAMPLE_RATE
    assert sd.streams[0].samplerate == 16000 and sd.streams[0].blocksize == FRAME_SIZE
    assert sd.streams[0].device == 1
    assert len(frames) == 40
    for frame, at in frames:
        assert frame.shape == (FRAME_SIZE,) and frame.dtype == np.float32
        assert at.tzinfo is not None
    assert all(a[1] <= b[1] for a, b in zip(frames, frames[1:], strict=False))
    assert abs(dominant_hz(np.concatenate([f for f, _ in frames])) - 220.0) < 10
    assert source.overflows == 0


def test_mic_source_default_device_and_native_rate_fallback(monkeypatch):
    sd = fake_sounddevice(supported_rates=(48000,))  # the host refuses 16 kHz
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    source = MicSource(None)
    frames = collect(source, 40)
    assert source.name == "Fake Mic"  # the default input
    assert source.native_rate == 48000
    assert sd.attempts == [16000, 48000]  # tried 16 kHz first
    assert [s.samplerate for s in sd.streams] == [48000]
    assert sd.streams[0].blocksize == FRAME_SIZE * 3 and sd.streams[0].device is None
    audio = np.concatenate([f for f, _ in frames])
    assert audio.shape == (40 * FRAME_SIZE,) and audio.dtype == np.float32
    assert abs(dominant_hz(audio) - 220.0) < 10
    assert abs(float(np.sqrt(np.mean(audio**2))) - 0.2 / np.sqrt(2)) < 0.02


def test_mic_source_unknown_device_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice())
    with pytest.raises(ValueError, match="no input device matching"):
        collect(MicSource("studio condenser"), 1)


def test_file_source_pads_to_whole_frames(tmp_path):
    wav = tmp_path / "short.wav"
    write_wav16k(wav, np.full(1000, 0.1, np.float32))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frames = list(FileSource(wav, start=start).frames(threading.Event()))
    assert len(frames) == 2
    assert frames[0][1] == start and frames[1][1] == start + timedelta(
        seconds=FRAME_SIZE / SAMPLE_RATE
    )
    assert frames[1][0].shape == (FRAME_SIZE,)
    assert np.all(frames[1][0][1000 - FRAME_SIZE :] == 0.0)
    stop = threading.Event()
    stop.set()
    assert list(FileSource(wav).frames(stop)) == []
