"""Helpers shared by the HF-style backends (unsloth, peft)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from soulsaka.config import TrainConfig

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

# ChatML markers used by the Qwen family; used to mask everything but the reply.
QWEN_INSTRUCTION_PART = "<|im_start|>user\n"
QWEN_RESPONSE_PART = "<|im_start|>assistant\n"


def chat_markers(base_model: str) -> tuple[str, str]:
    low = base_model.lower()
    if "qwen" in low:
        return QWEN_INSTRUCTION_PART, QWEN_RESPONSE_PART
    if "llama-3" in low or "llama3" in low:
        return (
            "<|start_header_id|>user<|end_header_id|>\n\n",
            "<|start_header_id|>assistant<|end_header_id|>\n\n",
        )
    if "gemma" in low:
        return "<start_of_turn>user\n", "<start_of_turn>model\n"
    return QWEN_INSTRUCTION_PART, QWEN_RESPONSE_PART


def count_lines(path: Path) -> int:
    with Path(path).open("rb") as fh:
        return sum(1 for line in fh if line.strip())


def steps_for(cfg: TrainConfig, n_examples: int) -> int:
    per_step = max(1, cfg.batch_size * cfg.grad_accum)
    return max(1, int(cfg.epochs * n_examples / per_step))


def write_metrics(out_dir: Path, metrics: dict[str, Any]) -> None:
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


class Timer:
    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.seconds = time.time() - self.t0
