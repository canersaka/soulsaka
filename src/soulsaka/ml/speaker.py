"""Speaker verification: is the voice on this clip the enrolled person?

An ECAPA-TDNN embedding (SpeechBrain) is compared against a running centroid stored
in ``speaker_profiles``. Cosine similarity above ``speaker.threshold`` counts as me.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from soulsaka.config import SpeakerConfig, detect_accelerator
from soulsaka.db import Database
from soulsaka.paths import models_dir
from soulsaka.util.time import now_iso

log = logging.getLogger(__name__)

ME = "me"


class SpeakerBackend(Protocol):
    name: str
    dim: int

    def embed(self, path: Path) -> np.ndarray: ...


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class FakeSpeakerBackend:
    """Deterministic embedding from a ``<audio>.spk`` sidecar label (or the file name)."""

    name = "fake"
    dim = 16

    def embed(self, path: Path) -> np.ndarray:
        sidecar = Path(path).with_suffix(".spk")
        label = sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else "me"
        seed = int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        return _unit(rng.normal(size=self.dim))


class SpeechBrainBackend:
    name = "speechbrain-ecapa"
    dim = 192

    def __init__(self, model: str, accelerator: str = "auto"):
        import torch  # type: ignore
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

        device = detect_accelerator(accelerator)
        if device == "mps":
            device = "cpu"  # some ECAPA ops are not implemented for MPS; CPU is fast enough
        savedir = models_dir() / "speechbrain" / model.replace("/", "__")
        self.clf = EncoderClassifier.from_hparams(
            source=model, savedir=str(savedir), run_opts={"device": device}
        )
        self.torch = torch
        self.name = f"speechbrain:{model}"

    def embed(self, path: Path) -> np.ndarray:
        from soulsaka.ml.audio import read_wav_mono16k

        samples = read_wav_mono16k(Path(path))
        wav = self.torch.from_numpy(samples).unsqueeze(0)
        with self.torch.no_grad():
            emb = self.clf.encode_batch(wav)
        return _unit(emb.squeeze().detach().cpu().numpy())


class SpeakerService:
    def __init__(self, backend: SpeakerBackend, cfg: SpeakerConfig):
        self.backend = backend
        self.cfg = cfg

    # -- profile storage ---------------------------------------------------------------
    def load_profile(self, db: Database, name: str = ME) -> tuple[np.ndarray, int] | None:
        row = db.one(
            "SELECT centroid, n_samples, dim, model FROM speaker_profiles WHERE name = ?", (name,)
        )
        if row is None or row["model"] != self.backend.name:
            return None
        vec = np.frombuffer(row["centroid"], dtype=np.float32)
        return _unit(vec), int(row["n_samples"])

    def save_profile(self, db: Database, centroid: np.ndarray, n: int, name: str = ME) -> None:
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO speaker_profiles(name, model, dim, centroid, n_samples, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET model = excluded.model, dim = excluded.dim,
                     centroid = excluded.centroid, n_samples = excluded.n_samples,
                     updated_at = excluded.updated_at""",
                (
                    name,
                    self.backend.name,
                    int(centroid.size),
                    _unit(centroid).astype(np.float32).tobytes(),
                    n,
                    now_iso(),
                ),
            )

    def reset_profile(self, db: Database, name: str = ME) -> None:
        with db.tx() as conn:
            conn.execute("DELETE FROM speaker_profiles WHERE name = ?", (name,))

    # -- operations --------------------------------------------------------------------
    def enroll(self, db: Database, paths: list[Path], name: str = ME) -> tuple[np.ndarray, int]:
        """Fold clips into the running centroid. Returns (centroid, n_samples)."""
        embs = [self.backend.embed(Path(p)) for p in paths]
        if not embs:
            raise ValueError("no clips to enroll")
        existing = self.load_profile(db, name)
        if existing is None:
            total = np.sum(embs, axis=0)
            n = len(embs)
        else:
            centroid, n0 = existing
            total = centroid * n0 + np.sum(embs, axis=0)
            n = n0 + len(embs)
        centroid = _unit(total)
        self.save_profile(db, centroid, n, name)
        return centroid, n

    def score(self, db: Database, path: Path, name: str = ME) -> float | None:
        prof = self.load_profile(db, name)
        if prof is None:
            return None
        emb = self.backend.embed(Path(path))
        return float(np.clip(np.dot(emb, prof[0]), -1.0, 1.0))

    def verify(self, db: Database, path: Path, name: str = ME) -> tuple[bool | None, float | None]:
        """(is_me, score). is_me is None while nobody is enrolled."""
        prof = self.load_profile(db, name)
        if prof is None or prof[1] < self.cfg.min_enroll_samples:
            return None, None
        s = self.score(db, path, name)
        if s is None:
            return None, None
        return s >= self.cfg.threshold, s

    def status(self, db: Database, name: str = ME) -> dict:
        prof = self.load_profile(db, name)
        return {
            "enrolled": prof is not None,
            "n_samples": 0 if prof is None else prof[1],
            "ready": prof is not None and prof[1] >= self.cfg.min_enroll_samples,
            "min_samples": self.cfg.min_enroll_samples,
            "threshold": self.cfg.threshold,
            "backend": self.backend.name,
        }


def build_speaker(cfg: SpeakerConfig, accelerator: str = "auto") -> SpeakerService:
    if cfg.backend == "fake":
        return SpeakerService(FakeSpeakerBackend(), cfg)
    return SpeakerService(SpeechBrainBackend(cfg.model, accelerator), cfg)
