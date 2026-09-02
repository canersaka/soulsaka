from __future__ import annotations

import sys
import threading

from soulsaka import bench
from soulsaka.client import HubClient
from soulsaka.config import LLMProfile


def test_bench_against_test_hub(client, runner, state):
    # A worker thread drains jobs so the benchmark can poll for completion like a real client.
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            if not runner.run_one():
                stop.wait(0.02)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        state.settings.llm.profiles["py"] = LLMProfile(
            backend="command",
            command=[sys.executable, "-c", "print('remember to rest')"],
            model="py",
        )
        hub = HubClient("http://testserver", client=client)
        report = bench.run(hub, n=2, chat_profiles=["py"])
    finally:
        stop.set()
        t.join(2)
    summary = report.summary()
    assert summary["text_capture_to_memory"]["n"] == 2
    assert all(s.ok for s in report.samples if s.name == "text_capture_to_memory")
    assert "chat_ttft" in summary and summary["chat_ttft"]["n"] == 1
    path = report.save()
    assert path.exists() and path.name.startswith("bench-")
