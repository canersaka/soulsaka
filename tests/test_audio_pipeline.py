from __future__ import annotations

import hashlib
from collections import deque

import numpy as np

from soulsaka.ml.asr import FakeASR
from soulsaka.ml.speaker import FakeSpeakerBackend, SpeakerService
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso
from tests.conftest import make_wav


class ScriptedSpeaker(FakeSpeakerBackend):
    """Returns embeddings for a scripted sequence of speaker labels."""

    def __init__(self, labels):
        self.labels = deque(labels)

    def embed(self, path):
        label = self.labels.popleft() if self.labels else "me"
        seed = int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        v = rng.normal(size=self.dim).astype(np.float32)
        return v / np.linalg.norm(v)


def _post_audio(client, tmp_path, origin="manual"):
    uid = new_uid()
    wav = make_wav(tmp_path / f"{uid}.wav")
    with wav.open("rb") as fh:
        r = client.post(
            "/api/captures/audio",
            data={"uid": uid, "client_ts": now_iso(), "origin": origin},
            files={"file": ("clip.wav", fh, "audio/wav")},
        )
    assert r.status_code == 201, r.text
    return uid


def test_audio_capture_enrolls_then_rejects_other_speaker(client, runner, state, tmp_path):
    settings = state.settings
    # enrollment: three manual clips (me), then the listener hears me and then someone else
    # embeddings are computed for: 3 enrollments, the listener clip of me, then the stranger
    backend = ScriptedSpeaker(["me", "me", "me", "me", "other"])
    state.set_service("speaker", SpeakerService(backend, settings.speaker))
    state.set_service("asr", FakeASR("remember to call the dentist tomorrow"))

    uids = [_post_audio(client, tmp_path) for _ in range(3)]
    runner.drain()
    for uid in uids:
        cap = client.get(f"/api/captures/{uid}").json()
        assert cap["status"] == "done" and cap["speaker_is_me"] is None  # nobody enrolled yet
        assert cap["duration_s"] and abs(cap["duration_s"] - 1.0) < 0.05
    status = client.get("/api/speaker").json()
    assert status["ready"] and status["n_samples"] == 3

    me_uid = _post_audio(client, tmp_path, origin="listener")
    runner.drain()
    cap = client.get(f"/api/captures/{me_uid}").json()
    assert cap["status"] == "done" and cap["speaker_is_me"] is True and cap["speaker_score"] > 0.9
    assert cap["memory_uids"], cap

    other_uid = _post_audio(client, tmp_path, origin="listener")
    runner.drain()
    cap = client.get(f"/api/captures/{other_uid}").json()
    assert cap["status"] == "discarded" and cap["speaker_is_me"] is False
    # discarded audio is deleted
    assert client.get(f"/api/captures/{other_uid}/audio").status_code == 404
    # my clip's audio is kept
    assert client.get(f"/api/captures/{me_uid}/audio").status_code == 200

    stats = client.get("/api/stats").json()
    assert stats["by_register"][0]["register"] == "speech"
    assert stats["me_messages"] == 4


def test_webm_upload_falls_back_gracefully(client, runner, tmp_path):
    uid = new_uid()
    r = client.post(
        "/api/captures/audio",
        data={"uid": uid, "client_ts": now_iso()},
        files={"file": ("clip.webm", b"not really audio", "audio/webm")},
    )
    assert r.status_code == 201
    runner.drain()
    cap = client.get(f"/api/captures/{uid}").json()
    assert cap["status"] in ("done", "discarded", "pending")
