"""Turn a trained adapter into something a server can load.

llama.cpp: ``convert_lora_to_gguf.py`` produces a small LoRA GGUF that ``llama-server
--lora`` applies on top of the base GGUF at load time (no merge, no re-quantisation).
Ollama: a Modelfile with ``ADAPTER`` pointing at the PEFT safetensors adapter.
MLX: nothing to convert; ``mlx_lm.server --adapter-path`` reads the adapter directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from soulsaka.config import TrainConfig


def find_llama_cpp(cfg: TrainConfig) -> Path | None:
    """Locate a llama.cpp checkout / install: config, env, or common places."""
    candidates = [cfg.llama_cpp_dir, os.environ.get("LLAMA_CPP_DIR", "")]
    candidates += [
        str(Path.home() / "llama.cpp"),
        str(Path.home() / "src" / "llama.cpp"),
        "/opt/llama.cpp",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def find_binary(name: str, cfg: TrainConfig) -> str | None:
    """A llama.cpp binary such as llama-server: PATH first, then the checkout's build dirs."""
    on_path = shutil.which(name)
    if on_path:
        return on_path
    root = find_llama_cpp(cfg)
    if root is None:
        return None
    for sub in ("build/bin", "build", "bin", "."):
        for suffix in ("", ".exe"):
            p = root / sub / f"{name}{suffix}"
            if p.exists():
                return str(p)
    return None


def convert_lora_to_gguf(
    adapter_dir: Path, base_model: str, out_path: Path, cfg: TrainConfig, *, log
) -> Path:
    """Run llama.cpp's converter. Needs the base model's HF files to read the architecture."""
    root = find_llama_cpp(cfg)
    script = None
    if root is not None and (root / "convert_lora_to_gguf.py").exists():
        script = root / "convert_lora_to_gguf.py"
    elif shutil.which("convert_lora_to_gguf.py"):
        script = Path(shutil.which("convert_lora_to_gguf.py"))  # type: ignore[arg-type]
    if script is None:
        raise RuntimeError(
            "convert_lora_to_gguf.py not found; set train.llama_cpp_dir to your llama.cpp checkout"
        )
    cmd = [
        sys.executable,
        str(script),
        "--base",
        base_model,
        "--outfile",
        str(out_path),
        "--outtype",
        "f16",
        str(adapter_dir),
    ]
    log("running: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"convert_lora_to_gguf failed: {proc.stderr[-800:]}")
    return out_path


def write_ollama_modelfile(adapter_dir: Path, base: str, out_path: Path, name: str) -> Path:
    """Modelfile for ``ollama create <name> -f Modelfile``.

    ``base`` may be an Ollama model tag (e.g. ``qwen3.5:4b``) or a path to a GGUF.
    Ollama applies safetensors LoRA adapters for the architectures it supports.
    """
    text = (
        f"FROM {base}\n"
        f"ADAPTER {adapter_dir}\n"
        "PARAMETER temperature 0.8\n"
        "PARAMETER num_ctx 4096\n"
        f"# created by soulsaka for adapter {name}\n"
    )
    out_path.write_text(text, encoding="utf-8")
    return out_path
