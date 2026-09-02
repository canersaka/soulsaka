"""Launch an OpenAI-compatible server that speaks with a given adapter version."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from soulsaka.config import Settings
from soulsaka.db import Database
from soulsaka.train import registry
from soulsaka.train.backends.mlx import mlx_model_name
from soulsaka.train.export import find_binary


def serve_command(
    db: Database,
    settings: Settings,
    version: str | None,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
) -> list[str]:
    """Build the argv for llama-server (CUDA/CPU) or mlx_lm.server (Apple Silicon)."""
    port = port or settings.train.serve_port
    run = registry.get_run(db, version) if version else registry.latest_done(db)
    if version and run is None:
        raise RuntimeError(f"no training run {version}")
    adapter = Path(run["adapter_path"]) if run and run.get("adapter_path") else None
    if run and run["backend"] == "mlx":
        cmd = [
            sys.executable,
            "-m",
            "mlx_lm",
            "server",
            "--model",
            mlx_model_name(run["base_model"]),
            "--host",
            host,
            "--port",
            str(port),
        ]
        if adapter:
            cmd += ["--adapter-path", str(adapter)]
        return cmd
    server = find_binary("llama-server", settings.train)
    if server is None:
        raise RuntimeError("llama-server not found; install llama.cpp or set train.llama_cpp_dir")
    base = settings.train.base_gguf or os.environ.get("SOULSAKA_BASE_GGUF", "")
    if not base:
        raise RuntimeError(
            "set train.base_gguf to the base model GGUF (e.g. Qwen3.5-4B-Q4_K_M.gguf)"
        )
    cmd = [
        server,
        "-m",
        base,
        "--host",
        host,
        "--port",
        str(port),
        "-c",
        "4096",
        "-ngl",
        "99",
        "--jinja",
    ]
    if run and run.get("gguf_path"):
        cmd += ["--lora", run["gguf_path"]]
    return cmd


def serve(
    db: Database,
    settings: Settings,
    version: str | None,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
) -> int:
    cmd = serve_command(db, settings, version, port=port, host=host)
    print("running: " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.call(cmd)
