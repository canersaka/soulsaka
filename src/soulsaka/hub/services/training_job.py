"""Run a training version in a child process so the hub keeps serving and the
training stack's GPU memory is released when it finishes."""

from __future__ import annotations

import subprocess
import sys

from soulsaka.hub.state import HubState
from soulsaka.paths import logs_dir
from soulsaka.train import registry


def run_training_subprocess(state: HubState, version: str, *, dry_run: bool = False) -> None:
    cmd = [sys.executable, "-m", "soulsaka.cli", "train", "run", "--version", version]
    if dry_run:
        cmd.append("--dry-run")
    log_path = logs_dir() / f"train-{version}.log"
    state.events.publish("training", version=version, status="running")
    with log_path.open("a", encoding="utf-8") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    run = registry.get_run(state.db, version)
    status = run["status"] if run else ("failed" if proc.returncode else "done")
    if proc.returncode != 0 and run and run["status"] not in ("failed", "done", "planned"):
        registry.mark_failed(
            state.db, version, f"training process exited with {proc.returncode}; see {log_path}"
        )
        status = "failed"
    state.events.publish("training", version=version, status=status)
