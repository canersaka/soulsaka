"""Background upload of spooled segments to the hub.

The uploader drains the spool oldest-first so the hub sees utterances in the order they
were spoken. A success is any 2xx, including the 200 the hub returns for a duplicate
``uid`` (an earlier attempt that landed after the client gave up on it); both files are
then deleted. Any exception keeps the files in place and backs off exponentially
(1 s, 2 s, ... 60 s) before the next attempt.

:class:`TranscriptPoller` is a cosmetic extra: after an upload it asks the hub what became
of the capture (transcript, speaker verdict) so the status display can show it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from soulsaka.client import CLIENT_HEADERS, HubError
from soulsaka.listener.spool import Spool, SpoolEntry
from soulsaka.util.time import parse_iso

log = logging.getLogger(__name__)

# Hub answers that will not change on retry: keep the file aside, move on.
QUARANTINE_STATUSES = frozenset({400, 404, 413, 415, 422})


class CaptureClient(Protocol):
    """The part of :class:`soulsaka.client.HubClient` the uploader needs."""

    def capture_audio(
        self,
        path: Path,
        *,
        uid: str | None = None,
        origin: str = "manual",
        client_ts: datetime | None = None,
        meta: dict | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class UploadStats:
    uploaded: int = 0
    failed: int = 0
    pending: int = 0
    last_error: str | None = None
    hub_reachable: bool | None = None  # None until the first attempt
    last_uid: str | None = None
    uploading: bool = False
    backoff_s: float = 0.0


class Uploader:
    """Drain a :class:`Spool` into the hub from a daemon thread.

    ``run_once`` does one attempt synchronously and is what the thread loops over; tests
    call it directly so nothing has to sleep.
    """

    def __init__(
        self,
        spool: Spool,
        client: CaptureClient,
        *,
        min_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        idle_poll_s: float = 5.0,
        on_uploaded: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.spool = spool
        self.client = client
        self.min_backoff_s = min_backoff_s
        self.max_backoff_s = max_backoff_s
        self.idle_poll_s = idle_poll_s
        self.on_uploaded = on_uploaded
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._uploaded = 0
        self._failed = 0
        self._quarantined = 0
        self._pending = spool.pending()
        self._last_error: str | None = None
        self._hub_reachable: bool | None = None
        self._last_uid: str | None = None
        self._uploading = False
        self._backoff_s = 0.0

    # -- thread control ----------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="soulsaka-uploader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def wake(self) -> None:
        """Tell the thread a new entry landed in the spool (upload it now, not in 5 s)."""
        self._wake.set()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_idle(self, timeout: float) -> bool:
        """Block until the spool is empty, an upload fails, or ``timeout`` passes.

        Returns True if the spool is empty. Used at shutdown to flush the last segment.
        """
        deadline = time.monotonic() + timeout
        with self._changed:
            while self.spool.pending() > 0 and self._backoff_s == 0 and self.alive:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
        return self.spool.pending() == 0

    # -- stats -------------------------------------------------------------------------
    def stats(self) -> UploadStats:
        with self._lock:
            return UploadStats(
                uploaded=self._uploaded,
                failed=self._failed,
                pending=self._pending,
                last_error=self._last_error,
                hub_reachable=self._hub_reachable,
                last_uid=self._last_uid,
                uploading=self._uploading,
                backoff_s=self._backoff_s,
            )

    # -- work --------------------------------------------------------------------------
    def run_once(self) -> bool:
        """Try to upload the oldest spooled segment. Returns True if one was uploaded."""
        entries = self.spool.entries()
        with self._lock:
            self._pending = len(entries)
        if not entries:
            return False
        entry = entries[0]
        try:
            meta = entry.read_meta()
        except (OSError, ValueError) as e:
            log.error("spool entry %s has an unreadable sidecar (%s); dropping it", entry.uid, e)
            self.spool.remove(entry)
            with self._changed:
                self._pending = max(0, self._pending - 1)
                self._changed.notify_all()
            return False
        with self._lock:
            self._uploading = True
        try:
            result = self.client.capture_audio(
                entry.wav,
                uid=entry.uid,
                origin=str(meta.get("origin") or "listener"),
                client_ts=parse_iso(str(meta["client_ts"])) if meta.get("client_ts") else None,
                meta=_meta_for_upload(meta),
            )
        except HubError as e:
            if e.status in QUARANTINE_STATUSES:
                # The hub understood the request and will never accept this file.
                self._quarantine(entry, e)
                return False
            self._record_failure(entry, e)
            return False
        except Exception as e:  # noqa: BLE001 - anything else means "keep the file, retry later"
            self._record_failure(entry, e)
            return False
        finally:
            with self._lock:
                self._uploading = False
        self.spool.remove(entry)
        with self._changed:
            self._uploaded += 1
            self._pending = max(0, self._pending - 1)
            self._backoff_s = 0.0
            self._hub_reachable = True
            self._last_error = None
            self._last_uid = entry.uid
            self._changed.notify_all()
        log.info("uploaded %s (%ss)", entry.uid, meta.get("duration_s", "?"))
        if self.on_uploaded is not None:
            try:
                self.on_uploaded(entry.uid, result)
            except Exception:  # noqa: BLE001 - a display hook must never break uploads
                log.exception("on_uploaded hook failed")
        return True

    def _quarantine(self, entry: SpoolEntry, exc: HubError) -> None:
        dest = self.spool.quarantine(entry, f"{type(exc).__name__}: {exc}")
        with self._changed:
            self._failed += 1
            self._quarantined += 1
            self._pending = max(0, self._pending - 1)
            self._last_error = f"rejected by hub, moved to {dest}: {exc}"
            self._hub_reachable = True
            self._changed.notify_all()
        log.error("hub rejected %s (%s); moved to %s", entry.uid, exc, dest)

    def _record_failure(self, entry: SpoolEntry, exc: Exception) -> None:
        with self._changed:
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            # HubError means the hub answered (and refused); anything else, it did not.
            self._hub_reachable = isinstance(exc, HubError)
            self._backoff_s = (
                min(self.max_backoff_s, self._backoff_s * 2)
                if self._backoff_s > 0
                else min(self.min_backoff_s, self.max_backoff_s)
            )
            backoff = self._backoff_s
            self._changed.notify_all()
        log.warning(
            "upload of %s failed (%s); retrying in %.0fs", entry.uid, self._last_error, backoff
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once():
                continue
            with self._lock:
                backoff = self._backoff_s
            if backoff > 0:
                self._stop.wait(backoff)  # a new segment does not shorten a backoff
            else:
                woke = self._wake.wait(self.idle_poll_s)
                self._wake.clear()
                if not woke:
                    self.spool.rescan()  # idle timeout: pick up anything left by another run


def _meta_for_upload(meta: dict[str, Any]) -> dict[str, Any] | None:
    extra = {k: v for k, v in meta.items() if k not in ("uid", "client_ts", "origin")}
    return extra or None


class TranscriptPoller:
    """Follow the last uploaded capture on the hub until it is processed.

    Best effort and purely cosmetic: errors are ignored, and a newer upload always wins.
    """

    FINAL = frozenset({"done", "discarded", "failed"})

    def __init__(
        self,
        hub_url: str,
        token: str = "",
        *,
        interval_s: float = 0.5,
        give_up_s: float = 20.0,
        timeout_s: float = 3.0,
    ) -> None:
        headers = dict(CLIENT_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=hub_url.rstrip("/"), headers=headers, timeout=timeout_s)
        self.interval_s = interval_s
        self.give_up_s = give_up_s
        self._queue: queue.Queue[str] = queue.Queue()
        self._latest: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def note(self, uid: str, _result: Any = None) -> None:
        """Uploader hook: start following ``uid``."""
        self._queue.put(uid)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="soulsaka-poller", daemon=True)
            self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._http.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                uid = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            while True:  # collapse a burst of uploads to the newest one
                try:
                    uid = self._queue.get_nowait()
                except queue.Empty:
                    break
            self._follow(uid)

    def _follow(self, uid: str) -> None:
        deadline = time.monotonic() + self.give_up_s
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                r = self._http.get(f"/api/captures/{uid}")
            except httpx.HTTPError:
                return
            if r.status_code != 200:
                return
            data = r.json()
            with self._lock:
                self._latest = data
            if data.get("status") in self.FINAL or not self._queue.empty():
                return
            self._stop.wait(self.interval_s)
