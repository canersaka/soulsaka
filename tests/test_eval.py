from __future__ import annotations

import sys

from soulsaka.config import LLMProfile
from soulsaka.eval import report
from soulsaka.eval.discriminator import cross_val_accuracy, run_discriminator
from soulsaka.eval.pairs import blind_summary, generate_pairs, pairs_for_rater
from soulsaka.eval.voice import run_voice_similarity
from soulsaka.train.dataset import build_snapshot
from tests.test_train_dataset import _seed


def _echo_profile(state, reply: str = "sounds good, see you at 8"):
    state.settings.llm.profiles["py"] = LLMProfile(
        backend="command", command=[sys.executable, "-c", f"print({reply!r})"], model="py"
    )


def test_pairs_rating_and_summary(client, state):
    _seed(state, n_convs=60)
    _echo_profile(state)
    build_snapshot(state.db, state.settings, "v1")
    uids = generate_pairs(state, "v1", n=6, profile="py", seed=1)
    assert len(uids) == 6
    shown = client.get(
        "/api/eval/pairs", params={"version": "v1", "rater": "ali"}, headers={"X-Soulsaka-Client": ""}
    ).json()
    assert len(shown) == 6 and {"uid", "context", "first", "second"} <= set(shown[0])
    for p in shown:
        r = client.post(
            f"/api/eval/pairs/{p['uid']}/guess",
            json={"rater": "ali", "guessed_first": p["first"] != "sounds good, see you at 8"},
            headers={"X-Soulsaka-Client": ""},
        )
        assert r.status_code == 200 and r.json()["correct"] is True
    assert pairs_for_rater(state.db, "v1", "ali") == []
    s = blind_summary(state.db, "v1")
    assert s["n"] == 6 and s["accuracy"] == 1.0 and s["raters"]["ali"]["correct"] == 6
    score = client.get(
        "/api/eval/pairs/v1/score", params={"rater": "ali"}, headers={"X-Soulsaka-Client": ""}
    ).json()
    assert score["accuracy"] == 1.0
    summary = client.get("/api/eval/summary").json()
    v1 = summary["versions"][0]
    assert v1["version"] == "v1" and v1["blind_accuracy"] == 1.0 and v1["blind_n"] == 6
    assert client.get("/api/eval/summary.svg").headers["content-type"].startswith("image/svg")


def test_discriminator_separates_obviously_fake_text(state):
    _seed(state, n_convs=240)
    _echo_profile(state, "ok")
    build_snapshot(state.db, state.settings, "v1")
    generate_pairs(state, "v1", n=12, profile="py", seed=2)
    result = run_discriminator(state, "v1", min_samples=10)
    assert result["accuracy"] >= 0.9
    acc, folds = cross_val_accuracy(["a b c"] * 10 + ["x y z"] * 10, [1] * 10 + [0] * 10)
    assert acc == 1.0 and len(folds) == 5


def test_voice_similarity_with_fakes(state, tmp_path):
    from soulsaka.ml.speaker import FakeSpeakerBackend, SpeakerService
    from tests.conftest import make_wav

    _seed(state, n_convs=5)
    speaker = SpeakerService(FakeSpeakerBackend(), state.settings.speaker)
    state.set_service("speaker", speaker)
    speaker.enroll(state.db, [make_wav(tmp_path / "me.wav")])
    out = run_voice_similarity(state, "v1", n=2)
    assert -1.0 <= out["cosine"] <= 1.0 and len(out["scores"]) == 2
    assert report.summary(state.db)["versions"][0]["voice_cosine"] == out["cosine"]
