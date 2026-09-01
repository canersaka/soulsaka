from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from soulsaka.config import get_settings, reset_settings
from soulsaka.hub.app import create_app
from soulsaka.hub.state import HubState
from soulsaka.ml.audio import write_wav16k


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("SOULSAKA_DATA_DIR", str(root))
    monkeypatch.setenv("SOULSAKA_ASR__BACKEND", "fake")
    monkeypatch.setenv("SOULSAKA_SPEAKER__BACKEND", "fake")
    monkeypatch.setenv("SOULSAKA_EMBED__BACKEND", "hash")
    monkeypatch.setenv("SOULSAKA_TTS__BACKEND", "fake")
    # An LLM profile nothing listens on, so extraction jobs skip quickly.
    monkeypatch.setenv("SOULSAKA_LLM__PROFILES__LOCAL__BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("SOULSAKA_ME__DISPLAY_NAME", "Caner")
    monkeypatch.setenv("SOULSAKA_ME__NAMES", '["Caner", "Caner Saka"]')
    monkeypatch.setenv("SOULSAKA_ME__EMAILS", '["me@example.com"]')
    reset_settings()
    yield root
    reset_settings()


@pytest.fixture
def state(data_dir):
    s = HubState(get_settings())
    yield s
    s.close()


@pytest.fixture
def client(state):
    app = create_app(get_settings(), state=state, start_workers=False)
    with TestClient(app, client=("127.0.0.1", 40000)) as c:
        c.headers["X-Soulsaka-Client"] = "test"
        yield c


@pytest.fixture
def runner(client):
    return client.app.state.runner


def make_wav(path, seconds: float = 1.0, freq: float = 220.0):
    t = np.arange(int(16000 * seconds)) / 16000.0
    write_wav16k(path, (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32))
    return path
