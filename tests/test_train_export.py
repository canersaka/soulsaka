from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from soulsaka.config import TrainConfig
from soulsaka.train import registry
from soulsaka.train.backends.mlx import mlx_model_name
from soulsaka.train.export import find_binary, find_llama_cpp, write_ollama_modelfile
from soulsaka.train.serve import serve_command


def test_mlx_model_name_prefers_quantised_community_build():
    assert mlx_model_name("Qwen/Qwen3.5-4B") == "mlx-community/Qwen3.5-4B-4bit"
    assert mlx_model_name("mlx-community/Qwen3.5-9B-8bit") == "mlx-community/Qwen3.5-9B-8bit"


def test_modelfile_points_at_adapter(tmp_path):
    out = write_ollama_modelfile(
        tmp_path / "adapter", "qwen3.5:4b", tmp_path / "Modelfile", "soulsaka-v1"
    )
    text = out.read_text()
    assert text.startswith("FROM qwen3.5:4b\n") and f"ADAPTER {tmp_path / 'adapter'}" in text


def test_find_llama_cpp_and_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("LLAMA_CPP_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert find_llama_cpp(TrainConfig()) is None
    root = tmp_path / "llama.cpp"
    (root / "build" / "bin").mkdir(parents=True)
    server = root / "build" / "bin" / "llama-server"
    server.write_text("#!/bin/sh\n")
    server.chmod(server.stat().st_mode | stat.S_IEXEC)
    cfg = TrainConfig(llama_cpp_dir=str(root))
    assert find_llama_cpp(cfg) == root
    assert find_binary("llama-server", cfg) == str(server)
    assert find_binary("llama-cli", cfg) is None


def test_serve_command_for_llama_and_mlx(state, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    root = tmp_path / "llama.cpp"
    (root / "build" / "bin").mkdir(parents=True)
    (root / "build" / "bin" / "llama-server").write_text("")
    cfg = state.settings.train
    cfg.llama_cpp_dir = str(root)
    cfg.base_gguf = str(tmp_path / "base.gguf")
    cfg.serve_extra_args = ["--chat-template-kwargs", '{"enable_thinking": false}']
    # no runs yet: serves the base model alone
    cmd = serve_command(state.db, state.settings, None, port=8080)
    assert (
        cmd[0].endswith("llama-server")
        and "--lora" not in cmd
        and cmd[-1] == '{"enable_thinking": false}'
    )
    registry.create_run(
        state.db, version="v1", backend="unsloth", base_model="Qwen/Qwen3.5-4B", config={}
    )
    registry.mark_done(
        state.db,
        "v1",
        adapter_path=str(tmp_path / "v1" / "adapter"),
        gguf_path=str(tmp_path / "v1.gguf"),
    )
    cmd = serve_command(state.db, state.settings, "v1")
    assert cmd[cmd.index("--lora") + 1] == str(tmp_path / "v1.gguf")
    registry.create_run(
        state.db, version="v2", backend="mlx", base_model="Qwen/Qwen3.5-4B", config={}
    )
    registry.mark_done(state.db, "v2", adapter_path=str(tmp_path / "v2" / "adapter"))
    cmd = serve_command(state.db, state.settings, "v2", port=9000)
    assert cmd[1:4] == ["-m", "mlx_lm", "server"] and "mlx-community/Qwen3.5-4B-4bit" in cmd
    assert (
        cmd[cmd.index("--adapter-path") + 1] == str(tmp_path / "v2" / "adapter") and "9000" in cmd
    )
    with pytest.raises(RuntimeError, match="no training run"):
        serve_command(state.db, state.settings, "v9")
    assert os.environ.get("PATH") == ""
