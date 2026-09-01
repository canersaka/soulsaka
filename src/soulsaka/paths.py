"""Filesystem layout of the local data directory.

Everything soulsaka stores lives under one directory (default ``~/.soulsaka``) so it is
obvious what to back up and what to delete. Override with ``SOULSAKA_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".soulsaka"


def data_dir() -> Path:
    return Path(os.environ.get("SOULSAKA_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()


def config_path() -> Path:
    return data_dir() / "config.toml"


def db_path() -> Path:
    return data_dir() / "soulsaka.db"


def audio_dir() -> Path:
    return data_dir() / "audio"


def spool_dir() -> Path:
    """Local buffer used by clients while the hub is unreachable."""
    return data_dir() / "spool"


def models_dir() -> Path:
    return data_dir() / "models"


def adapters_dir() -> Path:
    return data_dir() / "adapters"


def datasets_dir() -> Path:
    return data_dir() / "datasets"


def evals_dir() -> Path:
    return data_dir() / "evals"


def logs_dir() -> Path:
    return data_dir() / "logs"


def ensure_layout() -> Path:
    root = data_dir()
    for p in (
        root,
        audio_dir(),
        spool_dir(),
        models_dir(),
        adapters_dir(),
        datasets_dir(),
        evals_dir(),
        logs_dir(),
    ):
        p.mkdir(parents=True, exist_ok=True)
    return root
