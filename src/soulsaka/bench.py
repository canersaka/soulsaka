"""``soulsaka bench``: end-to-end latency of the paths a person actually feels.

* capture → memory: from POSTing "remember ..." until the memory exists on the hub
  (text) or from uploading a clip until it is verified, transcribed and stored (audio).
* chat: time to first token and tokens per second per LLM profile.

Numbers go to ``evals/bench-<timestamp>.json`` so changes (quantisation, model size,
NPU offload) can be compared honestly.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from soulsaka.client import HubClient, HubError
from soulsaka.paths import evals_dir
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso


@dataclass
class Sample:
    name: str
    seconds: float
    ok: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchReport:
    started_at: str
    hub: str
    samples: list[Sample] = field(default_factory=list)

    def summary(self) -> dict[str, dict[str, float | int]]:
        out: dict[str, dict[str, float | int]] = {}
        by_name: dict[str, list[float]] = {}
        for s in self.samples:
            if s.ok:
                by_name.setdefault(s.name, []).append(s.seconds)
        for name, vals in by_name.items():
            vals.sort()
            out[name] = {
                "n": len(vals),
                "p50_s": round(statistics.median(vals), 3),
                "p90_s": round(vals[min(len(vals) - 1, int(0.9 * len(vals)))], 3),
                "min_s": round(vals[0], 3),
                "max_s": round(vals[-1], 3),
            }
        return out

    def save(self, path: Path | None = None) -> Path:
        path = path or (evals_dir() / f"bench-{self.started_at[:19].replace(':', '-')}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({**asdict(self), "summary": self.summary()}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path


def _wait_done(
    client: HubClient, uid: str, timeout: float, poll: float = 0.05
) -> tuple[Any, float]:
    t0 = time.perf_counter()
    while True:
        cap = client.capture(uid)
        if cap.status in ("done", "discarded", "failed"):
            return cap, time.perf_counter() - t0
        if time.perf_counter() - t0 > timeout:
            return cap, time.perf_counter() - t0
        time.sleep(poll)


def bench_text_capture(
    client: HubClient, report: BenchReport, *, n: int = 5, timeout: float = 30.0
) -> None:
    for i in range(n):
        uid = new_uid()
        t0 = time.perf_counter()
        client.capture_text(f"remember the benchmark code is {4000 + i}", uid=uid)
        cap, _ = _wait_done(client, uid, timeout)
        total = time.perf_counter() - t0
        ok = cap.status == "done" and bool(cap.memory_uids)
        report.samples.append(Sample("text_capture_to_memory", total, ok, {"status": cap.status}))


def bench_audio_capture(
    client: HubClient, report: BenchReport, wav: Path, *, n: int = 3, timeout: float = 120.0
) -> None:
    for _ in range(n):
        uid = new_uid()
        t0 = time.perf_counter()
        client.capture_audio(wav, uid=uid, origin="manual")
        upload = time.perf_counter() - t0
        cap, _ = _wait_done(client, uid, timeout)
        total = time.perf_counter() - t0
        report.samples.append(
            Sample(
                "audio_capture_to_transcript",
                total,
                cap.status == "done",
                {
                    "upload_s": round(upload, 3),
                    "status": cap.status,
                    "duration_s": cap.duration_s,
                    "speaker_is_me": cap.speaker_is_me,
                },
            )
        )


def bench_chat(
    client: HubClient,
    report: BenchReport,
    *,
    profile: str | None = None,
    n: int = 3,
    prompt: str = "In one sentence, what should I remember about today?",
) -> None:
    for _ in range(n):
        t0 = time.perf_counter()
        first: float | None = None
        chars = 0
        try:
            for piece in client.chat_stream(prompt, profile=profile):
                if first is None:
                    first = time.perf_counter() - t0
                chars += len(piece)
        except HubError as e:
            report.samples.append(
                Sample("chat_ttft", 0.0, False, {"error": str(e), "profile": profile})
            )
            return
        total = time.perf_counter() - t0
        tokens = max(1, chars // 4)
        report.samples.append(Sample("chat_ttft", first or total, True, {"profile": profile}))
        report.samples.append(
            Sample("chat_total", total, True, {"profile": profile, "approx_tokens": tokens})
        )
        if total > (first or 0):
            report.samples.append(
                Sample(
                    "chat_tokens_per_s",
                    tokens / max(1e-6, total - (first or 0)),
                    True,
                    {"profile": profile},
                )
            )


def run(
    client: HubClient,
    *,
    n: int = 5,
    wav: Path | None = None,
    chat_profiles: list[str] | None = None,
    chat: bool = True,
) -> BenchReport:
    report = BenchReport(started_at=now_iso(), hub=client.base_url)
    bench_text_capture(client, report, n=n)
    if wav is not None:
        bench_audio_capture(client, report, wav, n=max(1, n // 2))
    if chat:
        for prof in chat_profiles or [None]:
            bench_chat(client, report, profile=prof, n=max(1, n // 2))
    return report
