"""mlx-lm LoRA on Apple Silicon (the MacBook path).

Shells out to ``python -m mlx_lm lora`` so the training process owns the memory and
goes away when it is done. Dataset directory already holds train/valid/test.jsonl in
the chat format mlx-lm understands.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from soulsaka.config import TrainConfig
from soulsaka.train.backends.common import Timer, count_lines, write_metrics

_LOSS_RE = re.compile(r"Iter (\d+): (?:Train|Val) loss ([\d.]+)")
_VAL_RE = re.compile(r"Iter (\d+): Val loss ([\d.]+)")
_TRAIN_RE = re.compile(r"Iter (\d+): Train loss ([\d.]+)")


def mlx_model_name(base_model: str) -> str:
    """Prefer a pre-quantised MLX community build of the same model when the config
    names a plain HF repo (4-bit keeps a 4B/8B model comfortable on 16 GB)."""
    if base_model.startswith("mlx-community/"):
        return base_model
    name = base_model.split("/")[-1]
    return f"mlx-community/{name}-4bit"


class MLXBackend:
    name = "mlx"

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("mlx_lm") is None:
            return False, "mlx-lm is not installed (pip install mlx-lm)"
        return True, "ok"

    def train(self, dataset_dir: Path, out_dir: Path, cfg: TrainConfig, *, log) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        adapter_dir = out_dir / "adapter"
        n_train = count_lines(dataset_dir / "train.jsonl")
        iters = max(50, int(cfg.epochs * n_train / max(1, cfg.batch_size)))
        model = mlx_model_name(cfg.base_model)
        cmd = [
            sys.executable,
            "-m",
            "mlx_lm",
            "lora",
            "--model",
            model,
            "--train",
            "--data",
            str(dataset_dir),
            "--adapter-path",
            str(adapter_dir),
            "--iters",
            str(iters),
            "--batch-size",
            str(cfg.batch_size),
            "--learning-rate",
            str(cfg.learning_rate),
            "--max-seq-length",
            str(cfg.max_seq_len),
            "--num-layers",
            "16",
            "--mask-prompt",
            "--steps-per-eval",
            "100",
            "--steps-per-report",
            "10",
            "--seed",
            str(cfg.seed),
            "--fine-tune-type",
            "lora",
        ]
        log("running: " + " ".join(cmd))
        train_losses: list[tuple[int, float]] = []
        val_losses: list[tuple[int, float]] = []
        with Timer() as t, (out_dir / "train.log").open("w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                logf.write(line)
                line = line.rstrip()
                if m := _TRAIN_RE.search(line):
                    train_losses.append((int(m.group(1)), float(m.group(2))))
                if m := _VAL_RE.search(line):
                    val_losses.append((int(m.group(1)), float(m.group(2))))
                if "loss" in line.lower():
                    log(line)
            rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"mlx_lm lora exited with {rc}; see {out_dir / 'train.log'}")
        # Record the LoRA hyper-parameters mlx used, for the registry.
        cfg_path = adapter_dir / "adapter_config.json"
        if cfg_path.exists():
            try:
                adapter_cfg = json.loads(cfg_path.read_text())
            except json.JSONDecodeError:
                adapter_cfg = {}
        else:
            adapter_cfg = {}
        metrics = {
            "train_loss": train_losses[-1][1] if train_losses else None,
            "eval_loss": val_losses[-1][1] if val_losses else None,
            "steps": iters,
            "wall_s": round(t.seconds, 1),
            "mlx_model": model,
            "lora_layers": adapter_cfg.get("num_layers"),
        }
        write_metrics(out_dir, metrics)
        return metrics
