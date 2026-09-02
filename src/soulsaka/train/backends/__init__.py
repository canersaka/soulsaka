"""QLoRA backends. Each one trains a LoRA adapter from the base model on a dataset
snapshot and writes it to an output directory in a standard layout:

    <out>/adapter/            HF/PEFT adapter (adapter_config.json, adapter_model.safetensors)
                              or MLX adapter (adapters.safetensors, adapter_config.json)
    <out>/metrics.json        train/eval loss, steps, wall time
    <out>/train.log           backend output
"""

from __future__ import annotations

import platform
from typing import Protocol

from soulsaka.config import TrainConfig


class TrainBackend(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def train(self, dataset_dir, out_dir, cfg: TrainConfig, *, log) -> dict: ...


def pick_backend(cfg: TrainConfig) -> TrainBackend:
    from soulsaka.train.backends.mlx import MLXBackend
    from soulsaka.train.backends.peft import PeftBackend
    from soulsaka.train.backends.unsloth import UnslothBackend

    if cfg.backend == "unsloth":
        return UnslothBackend()
    if cfg.backend == "mlx":
        return MLXBackend()
    if cfg.backend == "peft":
        return PeftBackend()
    # auto
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return MLXBackend()
    unsloth = UnslothBackend()
    if unsloth.available()[0]:
        return unsloth
    return PeftBackend()
