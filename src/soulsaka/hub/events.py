"""Tiny in-process event bus feeding the /api/events stream.

Workers publish from threads; SSE handlers drain per-subscriber queues.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from soulsaka.util.time import now_iso


@dataclass
class Event:
    event: str
    data: dict[str, Any]
    ts: str = field(default_factory=now_iso)

    def sse(self) -> str:
        payload = json.dumps({"event": self.event, "ts": self.ts, **self.data}, ensure_ascii=False)
        return f"event: {self.event}\ndata: {payload}\n\n"


class EventBus:
    def __init__(self, maxsize: int = 500):
        self._subs: set[queue.Queue[Event]] = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: str, /, **data: Any) -> None:
        ev = Event(event=event, data=data)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            # A slow consumer just misses events; it catches up through /api/sync.
            with contextlib.suppress(queue.Full):
                q.put_nowait(ev)
