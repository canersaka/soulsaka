"""Automated stand-in for the blind test: a classifier trained to tell my real replies
from the model's. If it cannot do better than chance, neither can a reader.

Uses scikit-learn when installed; otherwise a small numpy logistic regression over
hashed word and character n-grams, which is plenty for this job.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import numpy as np

from soulsaka.db import Database
from soulsaka.hub.state import HubState
from soulsaka.util.time import now_iso

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
DIM = 1 << 18


def _features(text: str) -> list[str]:
    toks = _WORD_RE.findall(text.casefold())
    feats = [f"w:{t}" for t in toks]
    feats += [f"b:{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
    padded = f" {text.casefold()} "
    for n in (3, 4, 5):
        feats += [f"c{n}:{padded[i : i + n]}" for i in range(len(padded) - n + 1)]
    feats.append(f"len:{min(len(toks) // 5, 20)}")
    return feats


def hashed_matrix(texts: list[str]) -> np.ndarray:
    m = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for f in _features(t):
            h = hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % DIM
            m[i, idx] += 1.0 if h[4] & 1 else -1.0
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class NumpyLogReg:
    def __init__(self, l2: float = 1e-3, epochs: int = 200, lr: float = 0.5):
        self.l2, self.epochs, self.lr = l2, epochs, lr
        self.w: np.ndarray | None = None
        self.b = 0.0

    def fit(self, x: np.ndarray, y: np.ndarray) -> NumpyLogReg:
        n, d = x.shape
        w = np.zeros(d, dtype=np.float32)
        b = 0.0
        for _ in range(self.epochs):
            z = x @ w + b
            p = 1.0 / (1.0 + np.exp(-z))
            g = p - y
            w -= self.lr * (x.T @ g / n + self.l2 * w)
            b -= self.lr * float(g.mean())
        self.w, self.b = w, b
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        assert self.w is not None
        return ((x @ self.w + self.b) > 0).astype(np.int64)


def cross_val_accuracy(
    texts: list[str], labels: list[int], folds: int = 5, seed: int = 7
) -> tuple[float, list[float]]:
    y = np.asarray(labels, dtype=np.float32)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(texts))
    fold_acc: list[float] = []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        from sklearn.linear_model import LogisticRegression  # type: ignore
        from sklearn.pipeline import FeatureUnion, make_pipeline  # type: ignore

        def make():
            return make_pipeline(
                FeatureUnion(
                    [
                        ("w", TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)),
                        (
                            "c",
                            TfidfVectorizer(
                                analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True
                            ),
                        ),
                    ]
                ),
                LogisticRegression(max_iter=2000, C=2.0),
            )

        arr = np.asarray(texts, dtype=object)
        for k in range(folds):
            test = idx[k::folds]
            train = np.setdiff1d(idx, test)
            clf = make().fit(arr[train], y[train])
            fold_acc.append(float((clf.predict(arr[test]) == y[test]).mean()))
        return float(np.mean(fold_acc)), fold_acc
    except ImportError:
        pass
    x = hashed_matrix(texts)
    for k in range(folds):
        test = idx[k::folds]
        train = np.setdiff1d(idx, test)
        clf = NumpyLogReg().fit(x[train], y[train])
        fold_acc.append(float((clf.predict(x[test]) == y[test]).mean()))
    return float(np.mean(fold_acc)), fold_acc


def real_texts(db: Database, version: str, limit: int) -> list[str]:
    rows = db.all(
        "SELECT real_text FROM eval_pairs WHERE version = ? ORDER BY id LIMIT ?", (version, limit)
    )
    return [r[0] for r in rows]


def run_discriminator(state: HubState, version: str, *, min_samples: int = 10) -> dict[str, Any]:
    """Train real-vs-model on this version's pairs; store the CV accuracy."""
    from soulsaka.eval.pairs import model_texts

    fake = [t for t in model_texts(state.db, version) if t.strip()]
    if len(fake) < min_samples:
        raise RuntimeError(
            f"{version}: need at least {min_samples} model replies (have {len(fake)}); run `soulsaka eval pairs` first"
        )
    real = real_texts(state.db, version, len(fake))
    n = min(len(real), len(fake))
    texts = real[:n] + fake[:n]
    labels = [1] * n + [0] * n
    acc, folds = cross_val_accuracy(texts, labels)
    details = {"n_real": n, "n_model": n, "folds": folds}
    with state.db.tx() as conn:
        conn.execute(
            "DELETE FROM eval_results WHERE version = ? AND kind = 'discriminator'", (version,)
        )
        conn.execute(
            "INSERT INTO eval_results(version, kind, metric, value, n, details, created_at) VALUES (?, 'discriminator', 'clf_accuracy', ?, ?, ?, ?)",
            (version, acc, 2 * n, json.dumps(details), now_iso()),
        )
    state.events.publish("eval", version=version, kind="discriminator", accuracy=acc)
    return {"version": version, "accuracy": acc, **details}
