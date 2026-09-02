from __future__ import annotations

import json
import re
import sys

import numpy as np
from typer.testing import CliRunner

from soulsaka.cli import app
from soulsaka.listener.cli import listen_app
from soulsaka.ml.audio import read_wav_mono16k, write_wav16k
from soulsaka.paths import logs_dir, spool_dir

SR = 16000


def write_speechlike_wav(path, bursts=((1.0, 1.0), (3.0, 1.5)), total_s=6.0):
    n = int(total_s * SR)
    x = np.zeros(n, np.float32)
    t = np.arange(n) / SR
    for start, dur in bursts:
        a, b = int(start * SR), int((start + dur) * SR)
        x[a:b] = (0.2 * np.sin(2 * np.pi * 220.0 * t[a:b])).astype(np.float32)
    write_wav16k(path, x)
    return path


def test_listen_file_no_upload_spools_segments(data_dir, tmp_path):
    wav = write_speechlike_wav(tmp_path / "speech.wav")
    result = CliRunner().invoke(
        app, ["listen", "file", str(wav), "--no-upload", "--vad", "energy", "--quiet"]
    )
    assert result.exit_code == 0, result.output
    assert "2 segment(s) captured" in result.output
    wavs = sorted(spool_dir().glob("*.wav"))
    sidecars = sorted(spool_dir().glob("*.json"))
    assert len(wavs) == 2 and len(sidecars) == 2
    assert not list(spool_dir().glob("*.tmp"))
    for wav_path, meta_path in zip(wavs, sidecars, strict=True):
        assert wav_path.stem == meta_path.stem
        meta = json.loads(meta_path.read_text())
        assert meta["uid"] == wav_path.stem and meta["origin"] == "listener"
        assert meta["device"] and meta["client_ts"].endswith("Z")
        assert abs(read_wav_mono16k(wav_path).shape[0] / SR - meta["duration_s"]) < 0.01
    durations = sorted(json.loads(m.read_text())["duration_s"] for m in sidecars)
    assert abs(durations[0] - 1.5) < 0.1 and abs(durations[1] - 2.0) < 0.1
    assert (logs_dir() / "listener.log").exists()
    assert "segment" in (logs_dir() / "listener.log").read_text()


def test_listen_file_live_display_runs_without_a_terminal(data_dir, tmp_path):
    wav = write_speechlike_wav(tmp_path / "speech.wav", bursts=((0.5, 1.0),), total_s=3.0)
    result = CliRunner().invoke(listen_app, ["file", str(wav), "--no-upload", "--vad", "auto"])
    assert result.exit_code == 0, result.output
    assert "1 segment(s) captured" in result.output
    assert len(list(spool_dir().glob("*.wav"))) == 1


def test_listen_requires_pairing_unless_no_upload(data_dir, tmp_path):
    wav = write_speechlike_wav(tmp_path / "speech.wav", bursts=(), total_s=1.0)
    result = CliRunner().invoke(app, ["listen", "file", str(wav)])
    assert result.exit_code == 1
    assert "not paired" in result.output
    assert not list(spool_dir().glob("*.wav"))


def test_listen_rejects_unknown_vad(data_dir, tmp_path):
    wav = write_speechlike_wav(tmp_path / "speech.wav", bursts=(), total_s=1.0)
    result = CliRunner().invoke(app, ["listen", "file", str(wav), "--no-upload", "--vad", "nope"])
    assert result.exit_code == 2
    assert "unknown VAD" in result.output


def test_listen_devices_explains_missing_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # makes `import sounddevice` fail
    result = CliRunner().invoke(app, ["listen", "devices"])
    assert result.exit_code == 2
    assert "uv sync --extra listener" in result.output


def test_listen_help_lists_subcommands():
    # Rich colours and wraps help text depending on the terminal; neutralise both.
    result = CliRunner().invoke(
        app, ["listen", "--help"], env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    for word in ("--device", "--vad", "--threshold", "--no-upload", "--spool-max-mb", "--quiet"):
        assert word in text
    assert "devices" in text and "file" in text
