"""Text embeddings for retrieval. sentence-transformers by default, an OpenAI-compatible
endpoint as an option, and a dependency-free hashing embedder as fallback/test double."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

import numpy as np

from soulsaka.config import EmbedConfig
from soulsaka.paths import models_dir

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class HashEmbedder:
    """Feature-hashed unigrams + bigrams. Crude, but deterministic and dependency-free."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _features(self, text: str) -> list[str]:
        toks = [t.casefold() for t in _TOKEN_RE.findall(text)]
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for f in self._features(text):
                h = hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % self.dim
                sign = 1.0 if h[4] & 1 else -1.0
                out[i, idx] += sign
        return _normalize_rows(out)


class SentenceTransformersEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model = SentenceTransformer(model, cache_folder=str(models_dir() / "st"))
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.name = f"st:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(
            texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False
        )
        return _normalize_rows(np.asarray(vecs, dtype=np.float32))


class OpenAIEmbedder:
    def __init__(self, base_url: str, model: str, dim: int, api_key: str = "none"):
        import httpx

        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=120)
        self.model = model
        self.dim = dim
        self.name = f"openai:{model}"
        self.headers = {"Authorization": f"Bearer {api_key or 'none'}"}

    def embed(self, texts: list[str]) -> np.ndarray:
        r = self.client.post(
            "/embeddings", json={"model": self.model, "input": texts}, headers=self.headers
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        m = np.asarray([d["embedding"] for d in data], dtype=np.float32)
        self.dim = m.shape[1]
        return _normalize_rows(m)


def build_embedder(cfg: EmbedConfig) -> Embedder:
    if cfg.backend == "hash":
        return HashEmbedder(cfg.dim)
    if cfg.backend == "openai":
        return OpenAIEmbedder(cfg.base_url, cfg.model, cfg.dim)
    try:
        return SentenceTransformersEmbedder(cfg.model)
    except Exception as e:  # noqa: BLE001
        log.warning("sentence-transformers unavailable (%s); using hash embeddings", e)
        return HashEmbedder(cfg.dim)
