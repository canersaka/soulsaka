from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from soulsaka.client import HubError
from soulsaka.listener.segmenter import SAMPLE_RATE, Segment
from soulsaka.listener.spool import Spool
from soulsaka.listener.uploader import Uploader
from soulsaka.ml.audio import read_wav_mono16k
from soulsaka.util.time import to_iso

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_segment(seconds: float = 0.5, offset_s: float = 0.0) -> Segment:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    samples = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    return Segment(samples=samples, started_at=T0 + timedelta(seconds=offset_s), duration_s=seconds)


class FakeClient:
    """Same ``capture_audio`` signature as HubClient; can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def capture_audio(self, path, *, uid=None, origin="manual", client_ts=None, meta=None):
        if self.error is not None:
            raise self.error
        path = Path(path)
        self.calls.append(
            {
                "uid": uid,
                "origin": origin,
                "client_ts": client_ts,
                "meta": meta,
                "bytes": path.stat().st_size,
            }
        )
        return {"uid": uid, "status": "pending"}


@pytest.fixture
def spool(tmp_path) -> Spool:
    return Spool(tmp_path / "spool")


def test_spool_writes_atomic_pairs(spool):
    seg = make_segment(0.75)
    entry = spool.write(seg, device="testbox")
    assert sorted(p.name for p in spool.root.iterdir()) == [f"{entry.uid}.json", f"{entry.uid}.wav"]
    meta = json.loads(entry.meta.read_text())
    assert meta == {
        "uid": entry.uid,
        "client_ts": to_iso(T0),
        "origin": "listener",
        "duration_s": 0.75,
        "device": "testbox",
    }
    assert meta["client_ts"].endswith("Z")
    audio = read_wav_mono16k(entry.wav)
    assert audio.shape[0] == seg.samples.shape[0]
    assert np.abs(audio - seg.samples).max() < 1e-3
    assert spool.entries() == [entry] and spool.pending() == 1
    assert entry.size == entry.wav.stat().st_size + entry.meta.stat().st_size


def test_spool_reindexes_from_disk_and_sweeps_leftovers(spool):
    entry = spool.write(make_segment())
    # a stale temp file, an old orphan wav, and a fresh orphan (a write in progress)
    old_tmp = spool.root / "x.wav.tmp"
    old_tmp.write_bytes(b"x")
    old_wav = spool.root / "orphan.wav"
    old_wav.write_bytes(b"x")
    past = time.time() - 3600
    os.utime(old_tmp, (past, past))
    os.utime(old_wav, (past, past))
    fresh = spool.root / "fresh.wav"
    fresh.write_bytes(b"x")
    reloaded = Spool(spool.root)
    assert [e.uid for e in reloaded.entries()] == [entry.uid]
    assert not old_tmp.exists() and not old_wav.exists() and fresh.exists()


def test_uploader_uploads_oldest_first_and_deletes_on_success(spool):
    # written out of order; the segment start time decides the upload order
    entries = {off: spool.write(make_segment(0.3, offset_s=off)) for off in (5.0, 1.0, 3.0)}
    client = FakeClient()
    uploader = Uploader(spool, client)
    assert uploader.stats().pending == 3 and uploader.stats().hub_reachable is None
    assert uploader.run_once() and uploader.run_once() and uploader.run_once()
    assert uploader.run_once() is False  # nothing left
    assert [c["uid"] for c in client.calls] == [
        entries[1.0].uid,
        entries[3.0].uid,
        entries[5.0].uid,
    ]
    first = client.calls[0]
    assert first["origin"] == "listener"
    assert first["client_ts"] == T0 + timedelta(seconds=1.0)
    assert first["meta"] == {"duration_s": 0.3, "device": first["meta"]["device"]}
    assert first["bytes"] > 44
    assert list(spool.root.iterdir()) == []
    st = uploader.stats()
    assert st.uploaded == 3 and st.pending == 0 and st.failed == 0
    assert st.hub_reachable is True and st.last_uid == entries[5.0].uid and st.backoff_s == 0


def test_uploader_keeps_files_and_backs_off_on_failure(spool):
    entry = spool.write(make_segment())
    client = FakeClient(error=ConnectionError("hub down"))
    uploader = Uploader(spool, client, min_backoff_s=1.0, max_backoff_s=60.0)
    expected = [1, 2, 4, 8, 16, 32, 60, 60]
    for i, backoff in enumerate(expected, start=1):
        assert uploader.run_once() is False
        st = uploader.stats()
        assert st.failed == i and st.uploaded == 0 and st.pending == 1
        assert st.backoff_s == backoff
        assert st.hub_reachable is False and "hub down" in (st.last_error or "")
    assert entry.wav.exists() and entry.meta.exists()
    # a hub that answers with an error counts as reachable, but still backs off
    client.error = HubError("POST /api/captures/audio: 500 boom")
    assert uploader.run_once() is False
    assert uploader.stats().hub_reachable is True and uploader.stats().backoff_s == 60
    # recovery resets the backoff and clears the files
    client.error = None
    assert uploader.run_once() is True
    st = uploader.stats()
    assert st.backoff_s == 0 and st.last_error is None and st.uploaded == 1 and st.pending == 0
    assert not entry.wav.exists() and not entry.meta.exists()


def test_uploader_drops_entry_with_unreadable_sidecar(spool):
    entry = spool.write(make_segment())
    entry.meta.write_text("not json")
    uploader = Uploader(spool, FakeClient())
    assert uploader.run_once() is False
    assert not entry.wav.exists() and not entry.meta.exists()
    assert uploader.stats().pending == 0


def test_spool_size_cap_deletes_oldest(tmp_path, caplog):
    probe = Spool(tmp_path / "probe")
    per_entry = probe.write(make_segment(0.5)).size
    capped = Spool(tmp_path / "spool", max_bytes=int(per_entry * 2.5))
    entries = [capped.write(make_segment(0.5, offset_s=i)) for i in range(4)]
    with caplog.at_level(logging.WARNING, logger="soulsaka.listener.spool"):
        pass
    kept = [e.uid for e in capped.entries()]
    assert kept == [entries[2].uid, entries[3].uid]
    assert not entries[0].wav.exists() and not entries[1].meta.exists()
    assert capped.size_bytes() <= capped.max_bytes
    warnings = [r for r in caplog.records if "deleting oldest segment" in r.getMessage()]
    assert len(warnings) == 2


def test_uploader_thread_drains_the_spool(spool):
    for i in range(3):
        spool.write(make_segment(0.2, offset_s=i))
    done = threading.Event()
    seen: list[str] = []

    def hook(uid, _result):
        seen.append(uid)
        if len(seen) == 3:
            done.set()

    uploader = Uploader(spool, FakeClient(), on_uploaded=hook)
    uploader.start()
    try:
        assert done.wait(timeout=10.0), "uploader thread did not drain the spool"
        assert uploader.wait_idle(timeout=5.0)
    finally:
        uploader.stop()
    assert spool.pending() == 0 and uploader.stats().uploaded == 3
    assert not uploader.alive


def test_uploader_against_a_real_hub(client, runner, tmp_path):
    """End to end: spool -> Uploader -> POST /api/captures/audio -> capture row -> job."""
    from soulsaka.client import HubClient

    hub = HubClient("http://testserver", client=client)
    spool = Spool(tmp_path / "spool")
    entry = spool.write(make_segment(1.0), device="macbook")
    uploader = Uploader(spool, hub)
    assert uploader.run_once() is True
    assert spool.pending() == 0 and not entry.wav.exists()
    cap = client.get(f"/api/captures/{entry.uid}").json()
    assert cap["kind"] == "audio" and cap["origin"] == "listener" and cap["status"] == "pending"
    assert cap["client_ts"] == to_iso(T0)
    assert cap["duration_s"] is not None and abs(cap["duration_s"] - 1.0) < 0.05
    runner.drain()
    assert client.get(f"/api/captures/{entry.uid}").json()["status"] in ("done", "discarded")

    # A re-upload of the same uid (the hub answers 200, not 201) is a success too:
    # the files must go, or the listener would resend it forever.
    again = spool.write(make_segment(0.5))
    again.wav.rename(spool.root / f"{entry.uid}.wav")
    again.meta.rename(spool.root / f"{entry.uid}.json")
    spool.rescan()
    assert spool.pending() == 1
    assert uploader.run_once() is True
    assert spool.pending() == 0 and list(spool.root.iterdir()) == []
    assert uploader.stats().uploaded == 2 and uploader.stats().hub_reachable is True
    assert len(client.get("/api/captures").json()) == 1


def test_uploader_quarantines_deterministic_rejections(tmp_path):
    from datetime import UTC, datetime

    import numpy as np

    from soulsaka.client import HubError
    from soulsaka.listener.segmenter import Segment
    from soulsaka.listener.spool import Spool
    from soulsaka.listener.uploader import Uploader

    class RejectingClient:
        calls = 0

        def capture_audio(self, path, **kwargs):
            self.calls += 1
            raise HubError("POST /api/captures/audio: 422 bad form", status=422)

    spool = Spool(tmp_path / "spool")
    entry = spool.write(Segment(np.zeros(16000, dtype=np.float32), datetime.now(UTC), 1.0))
    client = RejectingClient()
    up = Uploader(spool, client)
    assert up.run_once() is False
    assert client.calls == 1
    assert spool.entries() == []
    failed = tmp_path / "spool" / "failed"
    assert (failed / entry.wav.name).exists()
    assert (failed / f"{entry.uid}.error").read_text().startswith("HubError")
    # nothing left to retry, and no backoff was scheduled for a permanent rejection
    assert up.run_once() is False
