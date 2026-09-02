from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from soulsaka.client import ClientConfig
from soulsaka.importers.base import IMPORTERS, Importer, find_paths, run_import
from soulsaka.importers.sinks import DbSink, HubSink, open_sink
from soulsaka.models import ImportedMessage, ImportReport, MessageBatch, SourceRef


class FakeImporter(Importer):
    kind = "fake"
    label = "Fake"

    def __init__(self, n: int):
        super().__init__("fake-locator")
        self.n = n

    def iter_messages(self):
        for i in range(self.n):
            yield ImportedMessage(
                conversation_external_id=f"c{i % 3}",
                text=f"message {i}",
                ts=datetime(2024, 1, 1, tzinfo=UTC),
                is_me=True,
            )
        self.note("done streaming")


class RecordingSink:
    def __init__(self):
        self.chunks: list[int] = []

    def write(self, source: SourceRef, messages: list[ImportedMessage]) -> ImportReport:
        self.chunks.append(len(messages))
        return ImportReport(
            source=source,
            received=len(messages),
            inserted=len(messages),
            me_words=2 * len(messages),
            conversations=1,
        )


class StubClient:
    base_url = "http://hub:8765"

    def __init__(self):
        self.batches: list[MessageBatch] = []
        self.closed = False

    def push_messages(self, batch: MessageBatch) -> ImportReport:
        self.batches.append(batch)
        return ImportReport(source=batch.source, received=len(batch.messages), inserted=1)

    def stats(self):
        return SimpleNamespace(me_words=42)

    def close(self):
        self.closed = True


def test_run_import_streams_in_chunks():
    sink = RecordingSink()
    seen: list[int] = []
    report = run_import(FakeImporter(4500), sink, progress=seen.append)
    assert sink.chunks == [2000, 2000, 500]
    assert seen == [2000, 4000, 4500]
    assert report.received == 4500 and report.inserted == 4500 and report.me_words == 9000
    assert report.conversations == 3
    assert report.source == SourceRef(kind="fake", label="Fake", locator="fake-locator")
    assert report.notes == ["Fake: done streaming"]


def test_run_import_empty_source():
    sink = RecordingSink()
    report = run_import(FakeImporter(0), sink)
    assert sink.chunks == [] and report.received == 0 and report.conversations == 0


def test_registry_kinds_and_registers():
    assert set(IMPORTERS) == {
        "imessage", "whatsapp", "whatsapp_export", "mbox", "emlx", "imap", "discord", "git", "docs",
    }  # fmt: skip
    registers = {k: c.register for k, c in IMPORTERS.items()}
    for chat in ("imessage", "whatsapp", "whatsapp_export", "discord"):
        assert registers[chat] == "text"
    for mail in ("mbox", "emlx", "imap"):
        assert registers[mail] == "email"
        assert IMPORTERS[mail].source_kind == "email"
    assert registers["git"] == "doc" and registers["docs"] == "doc"
    assert IMPORTERS["whatsapp_export"].source_kind == "whatsapp"
    assert IMPORTERS["docs"].source_kind == "doc"
    assert "fake" not in IMPORTERS


def test_find_paths_depth_hidden_and_limit(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "x.txt").write_text("1")
    (tmp_path / "a" / "b" / "y.txt").write_text("1")
    (tmp_path / "a" / "b" / "c" / "z.txt").write_text("1")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "h.txt").write_text("1")
    found = find_paths([tmp_path], lambda p: p.suffix == ".txt", max_depth=3)
    assert {p.name for p in found} == {"x.txt", "y.txt"}
    assert len(find_paths([tmp_path], lambda p: p.suffix == ".txt", max_depth=4, limit=1)) == 1
    assert find_paths([tmp_path / "missing"], lambda p: True) == []


def test_hub_sink_posts_batches():
    client = StubClient()
    sink = HubSink(client)  # type: ignore[arg-type]
    report = run_import(FakeImporter(2500), sink)
    assert [len(b.messages) for b in client.batches] == [2000, 500]
    assert client.batches[0].source.kind == "fake"
    assert report.received == 2500 and report.inserted == 2
    assert sink.me_words() == 42
    assert "hub:8765" in sink.describe()
    sink.close()
    assert client.closed


def test_db_sink_is_idempotent(state):
    sink = DbSink(state)
    report = run_import(FakeImporter(5), sink)
    assert report.inserted == 5 and report.me_words == 10 and report.conversations == 3
    assert sink.me_words() == 10
    again = run_import(FakeImporter(5), sink)
    assert again.inserted == 0 and again.duplicates == 5


def test_open_sink_selection(data_dir):
    sink = open_sink(local=True)
    assert isinstance(sink, DbSink)
    sink.close()
    ClientConfig(hub_url="http://hub:8765", token="tok", device_uid="d").save()
    hub = open_sink()
    assert isinstance(hub, HubSink) and hub.client.base_url == "http://hub:8765"
    hub.close()
    other = open_sink(hub="http://other:1/")
    assert isinstance(other, HubSink) and other.client.base_url == "http://other:1"
    other.close()
    local = open_sink(local=True)
    assert isinstance(local, DbSink)
    local.close()


def test_hub_sink_against_a_real_hub(client, state):
    """The same batches the Mac would send, through the hub's actual endpoint."""
    from soulsaka.client import HubClient
    from soulsaka.db import corpus as corpus_db

    hub = HubClient("http://testserver")
    hub._c.close()
    hub._c = client  # the FastAPI TestClient is an httpx.Client; loopback needs no token
    sink = HubSink(hub)
    report = run_import(FakeImporter(2500), sink)
    assert report.received == 2500 and report.inserted == 2500 and report.me_words == 5000
    assert report.source.kind == "fake" and report.conversations == 3
    assert sink.me_words() == 5000
    again = run_import(FakeImporter(2500), sink)
    assert again.inserted == 0 and again.duplicates == 2500
    assert corpus_db.stats(state.db).me_words == 5000
    assert [s.kind for s in corpus_db.list_sources(state.db)] == ["fake"]
