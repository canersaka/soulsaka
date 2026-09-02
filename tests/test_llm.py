from __future__ import annotations

import sys

import pytest

from soulsaka.config import LLMConfig, LLMProfile, PrivacyConfig
from soulsaka.ml.llm import (
    ChatMessage,
    CloudRefused,
    LLMError,
    LLMRouter,
    extract_json,
    is_local_host,
)


def test_local_host_detection():
    assert is_local_host("127.0.0.1", [])
    assert is_local_host("192.168.1.20", [])
    assert is_local_host("hub.local", [])
    assert is_local_host("mybox", ["mybox"])
    assert not is_local_host("api.openai.com", [])


def test_cloud_gate():
    cfg = LLMConfig()
    router = LLMRouter(cfg, PrivacyConfig(allow_cloud_llm=False))
    with pytest.raises(CloudRefused):
        router.profile("claude")
    router = LLMRouter(cfg, PrivacyConfig(allow_cloud_llm=True))
    assert router.profile("claude")[1].backend == "anthropic"


def test_non_cloud_profile_must_be_local():
    cfg = LLMConfig(profiles={"sneaky": LLMProfile(base_url="https://api.example.com/v1")})
    router = LLMRouter(cfg, PrivacyConfig())
    with pytest.raises(LLMError):
        router.profile("sneaky")


def test_command_backend_roundtrip():
    prof = LLMProfile(
        backend="command",
        command=[sys.executable, "-c", "import sys; print('echo:' + sys.stdin.read()[-10:])"],
        model="py",
    )
    router = LLMRouter(LLMConfig(default="py", profiles={"py": prof}), PrivacyConfig())
    out = router.complete([ChatMessage("user", "hello there")])
    assert out.text.startswith("echo:") and "Assistant:" in out.text


def test_extract_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure! ```json\n{"a": [1,2]}\n```') == {"a": [1, 2]}
    assert extract_json('blah {"memories": []} trailing') == {"memories": []}
    with pytest.raises(ValueError):
        extract_json("nothing here")
