"""On-disk buffer of segments waiting to be uploaded.

Each segment is a pair of files in ``spool_dir()``: ``<uid>.wav`` (16 kHz mono PCM) and a
``<uid>.json`` sidecar holding the upload form fields::

    {"uid": ..., "client_ts": "<ISO UTC of segment start>", "origin": "listener",
     "duration_s": 2.5, "device": "<hostname>"}

Both files are written to a ``.tmp`` name and renamed into place, the sidecar last, so a
reader never sees a half-written entry. The spool is capped in size: past the cap the
oldest entries are deleted (and therefore lost) with a warning.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from soulsaka.listener.segmenter import Segment
from soulsaka.ml.audio import write_wav16k
from soulsaka.paths import spool_dir
from soulsaka.util.ids import new_uid
from soulsaka.util.time import to_iso

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 2048 << 20
# Half-written leftovers (a crash between the two renames) older than this are removed.
ORPHAN_AGE_S = 60.0


@dataclass(frozen=True)
class SpoolEntry:
    uid: str
    wav: Path
    meta: Path
    client_ts: str
    size: int

    def read_meta(self) -> dict[str, Any]:
        data = json.loads(self.meta.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("sidecar is not a JSON object")
        return data


def hostname() -> str:
    return platform.node() or "unknown"


class Spool:
    """Thread-safe index over the spool directory. Oldest entry first."""

    def __init__(self, root: Path | None = None, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.root = Path(root) if root is not None else spool_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes)
        self._lock = threading.RLock()
        self._index: dict[str, SpoolEntry] = {}
        self.rescan()

    # -- writing -----------------------------------------------------------------------
    def write(
        self, segment: Segment, *, device: str | None = None, origin: str = "listener"
    ) -> SpoolEntry:
        """Persist a segment atomically and return its entry."""
        uid = new_uid()
        wav = self.root / f"{uid}.wav"
        meta = self.root / f"{uid}.json"
        client_ts = to_iso(segment.started_at)
        sidecar = {
            "uid": uid,
            "client_ts": client_ts,
            "origin": origin,
            "duration_s": round(float(segment.duration_s), 3),
            "device": device or hostname(),
        }
        tmp_wav = self.root / f"{uid}.wav.tmp"
        tmp_meta = self.root / f"{uid}.json.tmp"
        write_wav16k(tmp_wav, segment.samples)
        os.replace(tmp_wav, wav)
        tmp_meta.write_text(json.dumps(sidecar), encoding="utf-8")
        os.replace(tmp_meta, meta)
        entry = SpoolEntry(
            uid=uid,
            wav=wav,
            meta=meta,
            client_ts=client_ts,
            size=wav.stat().st_size + meta.stat().st_size,
        )
        with self._lock:
            self._index[uid] = entry
        self.enforce_cap()
        return entry

    # -- reading -----------------------------------------------------------------------
    def entries(self) -> list[SpoolEntry]:
        """Complete entries, oldest first (by segment start time)."""
        with self._lock:
            return sorted(self._index.values(), key=lambda e: (e.client_ts, e.uid))

    def pending(self) -> int:
        with self._lock:
            return len(self._index)

    def size_bytes(self) -> int:
        with self._lock:
            return sum(e.size for e in self._index.values())

    # -- removal -----------------------------------------------------------------------
    def remove(self, entry: SpoolEntry) -> None:
        with self._lock:
            self._index.pop(entry.uid, None)
        for path in (entry.wav, entry.meta):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    def quarantine(self, entry: SpoolEntry, reason: str) -> Path:
        """Move an entry the hub will never accept out of the queue, keeping the files
        (plus a ``.error`` note) under ``failed/`` for inspection."""
        dest = self.root / "failed"
        dest.mkdir(parents=True, exist_ok=True)
        for path in (entry.wav, entry.meta):
            with contextlib.suppress(OSError):
                if path.exists():
                    os.replace(path, dest / path.name)
        with contextlib.suppress(OSError):
            (dest / f"{entry.uid}.error").write_text(reason, encoding="utf-8")
        self.remove(entry)
        return dest

    def enforce_cap(self) -> int:
        """Delete the oldest entries until the spool fits ``max_bytes``. Returns the count."""
        if self.max_bytes <= 0:
            return 0
        removed = 0
        with self._lock:
            while self._index and self.size_bytes() > self.max_bytes:
                oldest = self.entries()[0]
                log.warning(
                    "spool exceeds %d MB; deleting oldest segment %s from %s (never uploaded)",
                    self.max_bytes >> 20,
                    oldest.uid,
                    oldest.client_ts or "?",
                )
                self.remove(oldest)
                removed += 1
        return removed

    def rescan(self) -> None:
        """Rebuild the index from disk; also drops stale temp files and orphans."""
        now = time.time()
        index: dict[str, SpoolEntry] = {}
        with self._lock:
            for path in list(self.root.glob("*.tmp")):
                if _is_stale(path, now):
                    _unlink(path)
            for meta in sorted(self.root.glob("*.json")):
                uid = meta.stem
                wav = meta.with_suffix(".wav")
                if not wav.exists():
                    if _is_stale(meta, now):
                        _unlink(meta)
                    continue
                client_ts = ""
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        client_ts = str(data.get("client_ts") or "")
                except (OSError, ValueError):
                    pass  # kept; the uploader reports and drops it
                try:
                    size = wav.stat().st_size + meta.stat().st_size
                except OSError:
                    continue
                index[uid] = SpoolEntry(uid=uid, wav=wav, meta=meta, client_ts=client_ts, size=size)
            for wav in self.root.glob("*.wav"):
                if wav.stem not in index and _is_stale(wav, now):
                    _unlink(wav)
            self._index = index


def _is_stale(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > ORPHAN_AGE_S
    except OSError:
        return False


def _unlink(path: Path) -> None:
    log.warning("removing stale spool file %s", path.name)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
