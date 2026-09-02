from __future__ import annotations

from soulsaka.ml.asr import FakeASR
from soulsaka.ml.audio import wav_duration
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso
from soulsaka.voice import reference
from tests.conftest import make_wav


def _post_clip(client, tmp_path, seconds: float, text: str):
    uid = new_uid()
    wav = make_wav(tmp_path / f"{uid}.wav", seconds=seconds)
    client.app.state.hub.set_service("asr", FakeASR(text))
    with wav.open("rb") as fh:
        r = client.post(
            "/api/captures/audio",
            data={"uid": uid, "client_ts": now_iso(), "origin": "manual"},
            files={"file": ("c.wav", fh, "audio/wav")},
        )
    assert r.status_code == 201
    return uid


def test_reference_assembly_and_speak(client, runner, state, tmp_path):
    r = client.post("/api/voice/reference")
    assert r.status_code == 400  # nothing recorded yet
    for secs, text in (
        (3.0, "this is the first clip of my voice"),
        (4.0, "and here is another one for good measure"),
        (2.5, "one more short sentence here"),
    ):
        _post_clip(client, tmp_path, secs, text)
        runner.drain()
    r = client.post("/api/voice/reference")
    assert r.status_code == 200, r.text
    out = r.json()
    assert 6.0 <= out["seconds"] <= 12.5 and len(out["clips"]) >= 2
    assert out["reference_text"].count(".") == len(out["clips"])
    clip, text = reference.get_reference(state.db)
    assert clip == "voice/reference.wav" and text == out["reference_text"]
    assert abs(wav_duration(state.abs_path(clip)) - out["seconds"]) < 0.1
    assert client.get("/api/voice/reference/audio").status_code == 200
    assert client.get("/api/voice/reference").json()["candidates"] >= 3
    # fake TTS backend synthesizes something playable
    r = client.post("/api/voice/speak", json={"text": "hello from the clone"})
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("audio/wav")
        and len(r.content) > 1000
    )


def test_tts_dataset_export(client, runner, state, tmp_path):
    from soulsaka.voice.dataset import export_tts_dataset

    for secs, text in ((2.0, "first sentence of mine"), (3.0, "second one with more words in it")):
        _post_clip(client, tmp_path, secs, text)
        runner.drain()
    out = tmp_path / "tts"
    m = export_tts_dataset(state.db, state.root, out)
    assert m["clips"] == 2 and abs(m["seconds"] - 5.0) < 0.1 and not m["ready_for_finetune"]
    rows = (out / "metadata.csv").read_text().strip().splitlines()
    assert len(rows) == 2 and rows[0].endswith("|first sentence of mine")
    assert len(list((out / "wavs").glob("*.wav"))) == 2
