"""Orchestrates one version: snapshot -> train -> export -> registry."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from soulsaka.config import Settings
from soulsaka.db import Database
from soulsaka.paths import adapters_dir, datasets_dir
from soulsaka.train import registry
from soulsaka.train.backends import pick_backend
from soulsaka.train.dataset import build_snapshot
from soulsaka.train.export import convert_lora_to_gguf, write_ollama_modelfile

log = logging.getLogger(__name__)

Log = Callable[[str], None]


def _logger(out_dir: Path | None, echo: Log | None) -> Log:
    fh = (out_dir / "run.log").open("a", encoding="utf-8") if out_dir else None

    def _log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        if fh:
            fh.write(line + "\n")
            fh.flush()
        if echo:
            echo(line)
        else:
            log.info(msg)

    return _log


def plan_version(db: Database, settings: Settings, version: str | None = None) -> dict[str, Any]:
    version = version or registry.next_version(db)
    backend = pick_backend(settings.train)
    existing = registry.get_run(db, version)
    if existing and existing["status"] in ("running", "done"):
        raise RuntimeError(f"{version} already {existing['status']}")
    if existing:
        registry.update_run(
            db,
            version,
            status="planned",
            error=None,
            backend=backend.name,
            config=settings.train.model_dump(mode="json"),
        )
        return registry.get_run(db, version)  # type: ignore[return-value]
    return registry.create_run(
        db,
        version=version,
        backend=backend.name,
        base_model=settings.train.base_model,
        config=settings.train.model_dump(mode="json"),
    )


def run_version(
    db: Database,
    settings: Settings,
    version: str,
    *,
    dry_run: bool = False,
    echo: Log | None = None,
    export_gguf: bool = True,
) -> dict[str, Any]:
    """Build the snapshot and train. Records everything in ``training_runs``."""
    out_dir = adapters_dir() / version
    out_dir.mkdir(parents=True, exist_ok=True)
    say = _logger(out_dir, echo)
    if registry.get_run(db, version) is None:
        plan_version(db, settings, version)
    backend = pick_backend(settings.train)
    try:
        registry.mark_started(db, version)
        say(f"{version}: building dataset snapshot")
        ds_dir, manifest = build_snapshot(db, settings, version, out_dir=datasets_dir() / version)
        registry.update_run(
            db,
            version,
            dataset_path=str(ds_dir),
            dataset_hash=manifest.train_sha256,
            data_cutoff=manifest.data_cutoff,
            n_examples=manifest.n_examples,
            n_words=manifest.n_words,
            backend=backend.name,
        )
        say(
            f"{version}: {manifest.n_examples} examples / {manifest.n_words} words "
            f"({manifest.n_holdout} holdout) up to {manifest.data_cutoff}"
        )
        if manifest.n_examples == 0:
            raise RuntimeError("no training examples; import more of your messages first")
        if dry_run:
            registry.update_run(db, version, status="planned", finished_at=None, started_at=None)
            say(f"{version}: dry run, not training")
            return registry.get_run(db, version)  # type: ignore[return-value]
        ok, why = backend.available()
        if not ok:
            raise RuntimeError(f"backend {backend.name} unavailable: {why}")
        say(f"{version}: training with {backend.name} on {settings.train.base_model}")
        metrics = backend.train(ds_dir, out_dir, settings.train, log=say)
        adapter_dir = out_dir / "adapter"
        fields: dict[str, Any] = {"adapter_path": str(adapter_dir), "metrics": metrics}
        if export_gguf and backend.name != "mlx":
            try:
                gguf = convert_lora_to_gguf(
                    adapter_dir,
                    settings.train.base_model,
                    out_dir / f"{version}-lora.gguf",
                    settings.train,
                    log=say,
                )
                fields["gguf_path"] = str(gguf)
            except Exception as e:  # noqa: BLE001
                say(f"{version}: GGUF export skipped: {e}")
            try:
                write_ollama_modelfile(
                    adapter_dir,
                    settings.train.base_gguf or settings.train.base_model,
                    out_dir / "Modelfile",
                    f"soulsaka-{version}",
                )
            except Exception as e:  # noqa: BLE001
                say(f"{version}: Modelfile skipped: {e}")
        registry.mark_done(db, version, **fields)
        say(f"{version}: done {json.dumps(metrics)}")
        return registry.get_run(db, version)  # type: ignore[return-value]
    except Exception as e:
        registry.mark_failed(db, version, f"{type(e).__name__}: {e}")
        say(f"{version}: FAILED {type(e).__name__}: {e}")
        raise
