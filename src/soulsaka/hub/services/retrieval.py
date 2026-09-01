"""Hybrid retrieval over memories and my own messages: FTS5 + brute-force cosine."""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from soulsaka.db import memories as memories_db
from soulsaka.db.corpus import fts_query
from soulsaka.hub.state import HubState
from soulsaka.models import MemoryOut, MessageOut

log = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, int, np.ndarray, np.ndarray]] = {}
_CACHE_TTL = 5.0


def _embedder(state: HubState):
    return state.service("embedder")


def _to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def upsert_embedding(state: HubState, owner_kind: str, owner_id: int, text: str) -> None:
    emb = _embedder(state)
    vec = emb.embed([text])[0]
    with state.db.tx() as conn:
        conn.execute(
            """INSERT INTO embeddings(owner_kind, owner_id, model, dim, vec) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(owner_kind, owner_id, model) DO UPDATE SET vec = excluded.vec, dim = excluded.dim""",
            (owner_kind, owner_id, emb.name, int(vec.size), _to_blob(vec)),
        )


def embed_message(state: HubState, message_id: int) -> None:
    row = state.db.one("SELECT text FROM messages WHERE id = ? AND is_me = 1", (message_id,))
    if row:
        upsert_embedding(state, "message", message_id, row[0])


def embed_memory(state: HubState, uid: str) -> None:
    row = state.db.one("SELECT id, text FROM memories WHERE uid = ?", (uid,))
    if row:
        upsert_embedding(state, "memory", int(row[0]), row[1])


def backfill(state: HubState, *, limit: int = 500) -> dict[str, int]:
    """Embed rows that have no vector for the current model yet."""
    emb = _embedder(state)
    done = {"message": 0, "memory": 0}
    rows = state.db.all(
        """SELECT m.id, m.text FROM messages m
           LEFT JOIN embeddings e ON e.owner_kind = 'message' AND e.owner_id = m.id AND e.model = ?
           WHERE m.is_me = 1 AND e.owner_id IS NULL ORDER BY m.id DESC LIMIT ?""",
        (emb.name, limit),
    )
    if rows:
        vecs = emb.embed([r[1] for r in rows])
        with state.db.tx() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(owner_kind, owner_id, model, dim, vec) VALUES ('message', ?, ?, ?, ?)",
                [
                    (r[0], emb.name, int(v.size), _to_blob(v))
                    for r, v in zip(rows, vecs, strict=True)
                ],
            )
        done["message"] = len(rows)
    rows = state.db.all(
        """SELECT m.id, m.text FROM memories m
           LEFT JOIN embeddings e ON e.owner_kind = 'memory' AND e.owner_id = m.id AND e.model = ?
           WHERE e.owner_id IS NULL ORDER BY m.id DESC LIMIT ?""",
        (emb.name, limit),
    )
    if rows:
        vecs = emb.embed([r[1] for r in rows])
        with state.db.tx() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(owner_kind, owner_id, model, dim, vec) VALUES ('memory', ?, ?, ?, ?)",
                [
                    (r[0], emb.name, int(v.size), _to_blob(v))
                    for r, v in zip(rows, vecs, strict=True)
                ],
            )
        done["memory"] = len(rows)
    return done


def _matrix(state: HubState, owner_kind: str, model: str) -> tuple[np.ndarray, np.ndarray]:
    key = (owner_kind, model)
    count = int(
        state.db.scalar(
            "SELECT COUNT(*) FROM embeddings WHERE owner_kind = ? AND model = ?",
            (owner_kind, model),
        )
        or 0
    )
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[1] == count and now - hit[0] < _CACHE_TTL:
            return hit[2], hit[3]
    rows = state.db.all(
        "SELECT owner_id, vec FROM embeddings WHERE owner_kind = ? AND model = ?",
        (owner_kind, model),
    )
    if not rows:
        ids, mat = np.zeros(0, dtype=np.int64), np.zeros((0, 1), dtype=np.float32)
    else:
        ids = np.asarray([r[0] for r in rows], dtype=np.int64)
        mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    with _cache_lock:
        _cache[key] = (now, count, ids, mat)
    return ids, mat


def vector_search(state: HubState, owner_kind: str, query: str, k: int) -> list[tuple[int, float]]:
    emb = _embedder(state)
    ids, mat = _matrix(state, owner_kind, emb.name)
    if ids.size == 0:
        return []
    q = emb.embed([query])[0]
    scores = mat @ q
    top = np.argsort(-scores)[:k]
    return [(int(ids[i]), float(scores[i])) for i in top]


def _rrf(*rankings: list[int], k: int = 60) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank + 1)
    return fused


def search_memories(state: HubState, query: str, k: int = 8) -> list[MemoryOut]:
    if not query.strip():
        return memories_db.list_memories(state.db, limit=k)
    fts = [
        r[0]
        for r in state.db.all(
            """SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid
               WHERE memories_fts MATCH ? AND m.archived = 0 ORDER BY bm25(memories_fts) LIMIT ?""",
            (fts_query(query), k * 2),
        )
    ]
    try:
        vec = [i for i, _ in vector_search(state, "memory", query, k * 2)]
    except Exception as e:  # noqa: BLE001
        log.debug("vector search unavailable: %s", e)
        vec = []
    fused = _rrf(fts, vec)
    if not fused:
        return []
    ordered = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    marks = ",".join("?" for _ in ordered)
    rows = state.db.all(
        f"SELECT * FROM memories WHERE id IN ({marks}) AND archived = 0",
        tuple(i for i, _ in ordered),
    )
    by_id = {r["id"]: r for r in rows}
    out: list[MemoryOut] = []
    for i, score in ordered:
        r = by_id.get(i)
        if r is None:
            continue
        d = dict(r)
        d.pop("id")
        d.pop("meta", None)
        d["archived"] = bool(d["archived"])
        out.append(MemoryOut(**d, score=score))
    return out


def search_exemplars(
    state: HubState, query: str, k: int = 6, register: str | None = None
) -> list[MessageOut]:
    """My own past messages that resemble the query: style anchors for the prompt."""
    reg_clause = "AND m.register = ?" if register else ""
    params: list = [fts_query(query)]
    if register:
        params.append(register)
    params.append(k * 2)
    fts = [
        r[0]
        for r in state.db.all(
            f"""SELECT m.id FROM messages_fts f JOIN messages m ON m.id = f.rowid
                WHERE messages_fts MATCH ? AND m.is_me = 1 {reg_clause}
                ORDER BY bm25(messages_fts) LIMIT ?""",
            tuple(params),
        )
    ]
    try:
        vec = [i for i, _ in vector_search(state, "message", query, k * 2)]
    except Exception:  # noqa: BLE001
        vec = []
    fused = sorted(_rrf(fts, vec).items(), key=lambda kv: -kv[1])[:k]
    if not fused:
        return []
    marks = ",".join("?" for _ in fused)
    rows = state.db.all(
        f"""SELECT m.id, m.conversation_id, m.is_me, m.ts, m.register, m.lang, m.text, m.word_count
            FROM messages m WHERE m.id IN ({marks}) AND m.word_count BETWEEN 3 AND 120""",
        tuple(i for i, _ in fused),
    )
    by_id = {r["id"]: r for r in rows}
    out = []
    for i, _ in fused:
        r = by_id.get(i)
        if r is not None:
            out.append(MessageOut(**{**dict(r), "is_me": True}))
    return out
