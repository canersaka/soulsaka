"""Background worker: pulls jobs from the durable queue and runs handlers."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from soulsaka.db import jobs as jobs_db
from soulsaka.hub.state import HubState

log = logging.getLogger(__name__)

Handler = Callable[[HubState, dict], None]


class JobRunner:
    def __init__(self, state: HubState, poll_s: float = 0.5):
        self.state = state
        self.poll_s = poll_s
        self.handlers: dict[str, Handler] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    def run_one(self) -> bool:
        """Run a single queued job on the calling thread. Returns False if none was ready."""
        job = jobs_db.claim(self.state.db, list(self.handlers))
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"no handler for job kind {job.kind!r}")
            handler(self.state, job.payload)
        except Exception as e:  # noqa: BLE001
            log.warning("job %s (%s) failed: %s", job.id, job.kind, e)
            jobs_db.fail(self.state.db, job, f"{type(e).__name__}: {e}")
        else:
            jobs_db.complete(self.state.db, job.id)
        return True

    def drain(self, max_jobs: int = 1000) -> int:
        """Run everything that is ready (tests, CLI one-shots)."""
        n = 0
        while n < max_jobs and self.run_one():
            n += 1
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_one():
                    self._stop.wait(self.poll_s)
            except Exception:  # noqa: BLE001
                log.exception("job loop error")
                time.sleep(self.poll_s)

    def start(self, workers: int = 1) -> None:
        jobs_db.requeue_stale(self.state.db)
        for i in range(max(1, workers)):
            t = threading.Thread(target=self._loop, name=f"soulsaka-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()


def default_handlers() -> dict[str, Handler]:
    from soulsaka.hub.services import pipeline

    def process_capture(state: HubState, payload: dict) -> None:
        pipeline.process_capture(state, payload["uid"])

    def enroll_speaker(state: HubState, payload: dict) -> None:
        from soulsaka.hub.services.speaker_enroll import enroll_from_capture

        enroll_from_capture(state, payload["uid"])

    def extract_memories_llm(state: HubState, payload: dict) -> None:
        from soulsaka.hub.services.extract_llm import extract_from_capture

        extract_from_capture(state, payload["uid"])

    def embed_message(state: HubState, payload: dict) -> None:
        from soulsaka.hub.services.retrieval import embed_message

        if payload.get("message_id"):
            embed_message(state, int(payload["message_id"]))

    def embed_memory(state: HubState, payload: dict) -> None:
        from soulsaka.hub.services.retrieval import embed_memory

        embed_memory(state, payload["uid"])

    return {
        "process_capture": process_capture,
        "enroll_speaker": enroll_speaker,
        "extract_memories_llm": extract_memories_llm,
        "embed_message": embed_message,
        "embed_memory": embed_memory,
    }
