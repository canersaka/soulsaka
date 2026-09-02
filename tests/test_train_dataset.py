from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from soulsaka.db import corpus as corpus_db
from soulsaka.models import ImportedMessage, SourceRef
from soulsaka.train import registry
from soulsaka.train.dataset import build_snapshot, build_turns, iter_examples, preview, read_jsonl
from soulsaka.train.prompting import system_prompt


def _seed(state, n_convs: int = 3):
    src = SourceRef(kind="whatsapp", label="WhatsApp", locator="x")
    msgs = []
    t0 = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    for c in range(n_convs):
        t = t0 + timedelta(days=c)
        conv = f"chat{c}"
        msgs += [
            ImportedMessage(
                conversation_external_id=conv,
                text=f"hey are you coming tonight? ({c})",
                ts=t,
                is_me=False,
                sender_handle=f"+1617555010{c}",
                sender_name="Ali",
            ),
            ImportedMessage(
                conversation_external_id=conv, text="yeah", ts=t + timedelta(minutes=1), is_me=True
            ),
            ImportedMessage(
                conversation_external_id=conv,
                text=f"probably around {c % 12}, still finishing the lab",
                ts=t + timedelta(minutes=2),
                is_me=True,
            ),
            ImportedMessage(
                conversation_external_id=conv,
                text=f"cool, bring the cable number {c}",
                ts=t + timedelta(minutes=5),
                is_me=False,
                sender_handle=f"+1617555010{c}",
                sender_name="Ali",
            ),
            ImportedMessage(
                conversation_external_id=conv,
                text="<Media omitted>",
                ts=t + timedelta(minutes=6),
                is_me=True,
            ),
            ImportedMessage(
                conversation_external_id=conv,
                text=f"got it, see you there in {c} minutes",
                ts=t + timedelta(minutes=7),
                is_me=True,
            ),
        ]
    corpus_db.ingest_messages(state.db, state.salt, src, msgs)
    git = SourceRef(kind="git", label="git", locator="~/code")
    corpus_db.ingest_messages(
        state.db,
        state.salt,
        git,
        [
            ImportedMessage(
                conversation_external_id="repo1",
                conversation_title="repo1",
                text="Fix off-by-one in the ring buffer when the segment wraps",
                ts=t0,
                is_me=True,
                register="doc",
            )
        ],
    )


def test_build_turns_merges_bursts():
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    msgs = [
        {"is_me": 0, "ts": t0.isoformat(), "text": "a", "sender_name": "Ali"},
        {"is_me": 1, "ts": (t0 + timedelta(minutes=1)).isoformat(), "text": "b"},
        {"is_me": 1, "ts": (t0 + timedelta(minutes=2)).isoformat(), "text": "c"},
        {"is_me": 1, "ts": (t0 + timedelta(hours=5)).isoformat(), "text": "d"},  # new burst
    ]
    turns = build_turns(msgs, is_group=False)
    assert [t.role for t in turns] == ["user", "assistant", "assistant"]
    assert turns[1].text == "b\nc" and turns[2].text == "d"
    group = build_turns(
        [
            {"is_me": 0, "ts": t0.isoformat(), "text": "a", "sender_name": "Ali"},
            {"is_me": 0, "ts": t0.isoformat(), "text": "x", "sender_name": "Bo"},
        ],
        is_group=True,
    )
    assert group[0].text == "Ali: a\nBo: x" and group[0].names == ["Ali", "Bo"]


def test_examples_have_me_as_only_target(state):
    _seed(state)
    exs = list(iter_examples(state.db, state.settings))
    assert exs, "no examples built"
    for ex, _holdout in exs:
        msgs = ex.messages
        assert msgs[0]["role"] == "system" and "You are Caner" in msgs[0]["content"]
        assert msgs[-1]["role"] == "assistant"
        # strict alternation after the system prompt
        roles = [m["role"] for m in msgs[1:]]
        assert roles[-2] == "user"
        assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
        assert "Media omitted" not in msgs[-1]["content"]
    targets = [ex.messages[-1]["content"] for ex, _ in exs]
    assert "yeah\nprobably around 0, still finishing the lab" in targets
    assert "got it, see you there in 0 minutes" in targets
    doc = [ex for ex, _ in exs if ex.meta["register"] == "doc"]
    assert doc and doc[0].messages[1]["content"].startswith("Write the next piece for: repo1")
    assert "Register: doc" in doc[0].messages[0]["content"]


def test_snapshot_and_manifest(state, tmp_path):
    _seed(state, n_convs=40)
    out, m = build_snapshot(state.db, state.settings, "v1", out_dir=tmp_path / "v1")
    assert (
        (out / "train.jsonl").exists()
        and (out / "valid.jsonl").exists()
        and (out / "test.jsonl").exists()
    )
    rows = list(read_jsonl(out / "train.jsonl"))
    assert len(rows) == m.n_examples and m.n_examples > 0
    assert m.n_holdout > 0  # 5% of 40 conversations lands a couple in holdout
    assert m.n_examples + m.n_holdout == 81  # 2 per conversation + 1 commit message
    assert m.train_sha256 and m.data_cutoff
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["version"] == "v1" and manifest["config"]["context_window"] == 8
    # deterministic
    out2, m2 = build_snapshot(state.db, state.settings, "v1", out_dir=tmp_path / "v1b")
    assert m2.train_sha256 == m.train_sha256


def test_preview_and_registry(state):
    _seed(state)
    p = preview(state.db, state.settings, n=2)
    assert p["n_examples"] >= 4 and len(p["samples"]) == 2
    assert p["samples"][0]["target"]
    assert registry.next_version(state.db) == "v1"
    run = registry.create_run(
        state.db, version="v1", backend="peft", base_model="x", config={"a": 1}
    )
    assert run["status"] == "planned" and run["config"] == {"a": 1}
    registry.mark_started(state.db, "v1")
    registry.mark_done(state.db, "v1", metrics={"train_loss": 1.5}, adapter_path="/tmp/a")
    assert registry.latest_done(state.db)["metrics"]["train_loss"] == 1.5
    assert registry.next_version(state.db) == "v2"
    assert corpus_db.stats(state.db).latest_version == "v1"


def test_system_prompt_mentions_register_and_language():
    s = system_prompt("Caner", register="email", lang="tr", setting="email thread: lab report")
    assert "Register: email" in s and "Turkish" in s and "lab report" in s


def test_training_api(client, state):
    _seed(state)
    assert client.get("/api/training/runs").json() == []
    p = client.get("/api/training/dataset/preview", params={"n": 1}).json()
    assert p["n_examples"] >= 4
    r = client.post("/api/training/runs", json={"dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "v1" and r.json()["status"] == "planned"
    assert (
        client.post("/api/training/runs", json={"version": "v1"}).status_code == 200
    )  # replan allowed while planned
