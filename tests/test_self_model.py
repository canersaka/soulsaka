from __future__ import annotations

import sys

from soulsaka.config import LLMProfile
from soulsaka.hub.services import self_model as sm
from soulsaka.hub.services.chat import build_messages
from tests.test_train_dataset import _seed


def test_fingerprint_and_regenerate_without_llm(state):
    _seed(state, n_convs=5)
    stats = sm.style_stats(state.db)
    assert stats["n"] > 0 and "lab" in stats["top_words"]
    doc = sm.regenerate(state, use_llm=False)
    assert doc.startswith("# Caner") and "Style fingerprint" in doc
    assert sm.current(state) == doc
    msgs = build_messages(state, "hey", [], mode="twin")
    assert "Style fingerprint" in msgs[0].content


def test_regenerate_with_llm_narrative(state):
    _seed(state, n_convs=3)
    state.settings.llm.profiles["local"] = LLMProfile(
        backend="command",
        command=[sys.executable, "-c", "print('Caner texts in short bursts and says tbh a lot.')"],
        model="py",
    )
    doc = sm.regenerate(state, use_llm=True)
    assert "## Profile" in doc and "short bursts" in doc


def test_self_model_api(client, runner, state):
    _seed(state, n_convs=2)
    assert client.get("/api/self-model").json()["markdown"] == ""
    client.post("/api/self-model/regenerate")
    runner.drain()
    assert "Style fingerprint" in client.get("/api/self-model").json()["markdown"]
