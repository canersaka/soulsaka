from __future__ import annotations

from soulsaka.db import jobs as jobs_db
from soulsaka.hub.jobs import JobRunner


def test_queue_claim_complete(state):
    jobs_db.enqueue(state.db, "a", {"x": 1})
    jobs_db.enqueue(state.db, "b", {"x": 2}, priority=5)
    job = jobs_db.claim(state.db)
    assert job.kind == "b"  # higher priority first
    jobs_db.complete(state.db, job.id)
    job = jobs_db.claim(state.db)
    assert job.kind == "a"
    assert jobs_db.claim(state.db) is None


def test_failure_backoff_and_exhaustion(state):
    jobs_db.enqueue(state.db, "a", {}, max_attempts=2)
    job = jobs_db.claim(state.db)
    jobs_db.fail(state.db, job, "boom", backoff_s=0)
    assert jobs_db.counts(state.db) == {"queued": 1}
    job = jobs_db.claim(state.db)
    assert job.attempts == 2
    jobs_db.fail(state.db, job, "boom again", backoff_s=0)
    assert jobs_db.counts(state.db) == {"failed": 1}


def test_runner_dispatch(state):
    runner = JobRunner(state)
    seen = []
    runner.register("hello", lambda st, payload: seen.append(payload["n"]))
    for i in range(3):
        jobs_db.enqueue(state.db, "hello", {"n": i})
    assert runner.drain() == 3
    assert seen == [0, 1, 2]
    assert jobs_db.counts(state.db) == {"done": 3}
