from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from soulsaka.db import corpus as corpus_db
from soulsaka.importers.base import ImporterError, run_import
from soulsaka.importers.imessage import IMessageImporter, attributed_body_text
from soulsaka.importers.sinks import DbSink
from soulsaka.importers.snapshot import SqliteSnapshot, apple_time
from tests.fixtures.importers import ALI_PHONE, LONG_TEXT, T0, apple_ns, make_chat_db, typedstream


def test_attributed_body_parser():
    assert attributed_body_text(typedstream("hello")) == "hello"
    assert attributed_body_text(typedstream(LONG_TEXT)) == LONG_TEXT  # 0x81 + uint16 length
    assert attributed_body_text(typedstream("çok iyi 😀")) == "çok iyi 😀"
    big = "x" * 70_000  # 0x82 + uint32 length
    assert attributed_body_text(typedstream(big)) == big
    assert attributed_body_text(None) is None
    assert attributed_body_text(b"") is None
    assert attributed_body_text(b"garbage without markers") is None
    assert attributed_body_text(b"NSString\x01\x94\x84\x01+") is None


def test_apple_time_seconds_and_nanoseconds():
    assert apple_time(0) == datetime(2001, 1, 1, tzinfo=UTC)
    assert apple_time(None) == datetime(2001, 1, 1, tzinfo=UTC)
    assert apple_time(T0.timestamp() - 978_307_200) == T0
    assert abs((apple_time(apple_ns(T0)) - T0).total_seconds()) < 0.001


def test_sqlite_snapshot_copies_sidecars_and_is_read_only(tmp_path):
    db = make_chat_db(tmp_path / "chat.db")
    (tmp_path / "chat.db-wal").write_bytes(b"")
    (tmp_path / "chat.db-shm").write_bytes(b"")
    snap = SqliteSnapshot(db, prefix="soulsaka-test-")
    with snap as conn:
        tempdir = snap.tempdir
        assert tempdir is not None and (tempdir / "chat.db-wal").exists()
        assert conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 10
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM message")
    assert not tempdir.exists()
    with pytest.raises(FileNotFoundError):
        SqliteSnapshot(tmp_path / "nope.db").__enter__()


def test_sqlite_snapshot_sees_uncheckpointed_wal_rows(tmp_path):
    db = make_chat_db(tmp_path / "chat.db")
    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO handle VALUES (99, 'late@example.com', 'iMessage')")
    live.commit()  # sits in chat.db-wal until a checkpoint; Messages.app keeps it open
    assert (tmp_path / "chat.db-wal").stat().st_size > 0
    with SqliteSnapshot(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM handle").fetchone()[0] == 4
    live.close()


@pytest.fixture(params=[True, False], ids=["nanoseconds", "seconds"])
def chat_db(tmp_path, request):
    return make_chat_db(tmp_path / "chat.db", nanoseconds=request.param)


def test_imessage_import(chat_db):
    msgs = list(IMessageImporter(chat_db).iter_messages())
    by_guid = {m.external_id: m for m in msgs}
    # tapback (g3), group event (g4), app balloon (g7) and attachment-only (g10) are skipped
    assert list(by_guid) == ["g1", "g2", "g5", "g6", "g8", "g9"]
    assert all(m.register == "text" for m in msgs)

    ali = by_guid["g1"]
    assert not ali.is_me and ali.sender_handle == ALI_PHONE and ali.sender_name is None
    assert ali.conversation_external_id == f"iMessage;-;{ALI_PHONE}"
    assert ali.conversation_title == ALI_PHONE and not ali.is_group
    assert ali.ts.tzinfo is UTC and abs((ali.ts - T0).total_seconds()) < 0.001

    me = by_guid["g2"]  # NULL text, body decoded from attributedBody
    assert me.is_me and me.text == "not much, grinding for the exam tbh"
    assert me.sender_handle is None and me.sender_name is None

    group = by_guid["g5"]
    assert group.is_me and group.is_group and group.conversation_title == "Trip crew"
    assert group.conversation_external_id == "iMessage;+;chat123"

    bob = by_guid["g6"]
    assert bob.text == LONG_TEXT and bob.sender_handle == "bob@example.com" and not bob.is_me
    assert abs((bob.ts - (T0 + timedelta(minutes=5))).total_seconds()) < 0.001

    assert by_guid["g8"].text == "https://example.com/x"  # URL balloon kept
    orphan = by_guid["g9"]  # no chat_message_join row
    assert orphan.conversation_external_id == "handle:+905320000000"
    assert orphan.conversation_title == "+905320000000"


def test_imessage_estimate_and_discover(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = make_chat_db(home / "Library" / "Messages" / "chat.db")
    assert IMessageImporter.estimate(db) == 7

    (found,) = IMessageImporter.discover(home, "Darwin")
    assert found.available and found.estimate == 7 and found.locator == str(db)
    assert found.kind == "imessage" and found.importer_kind == "imessage"
    (linux,) = IMessageImporter.discover(home, "Linux")
    assert not linux.available and "macOS" in linux.reason
    (missing,) = IMessageImporter.discover(tmp_path / "empty", "Darwin")
    assert not missing.available and "not found" in missing.reason

    def denied(self):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(SqliteSnapshot, "__enter__", denied)
    (fda,) = IMessageImporter.discover(home, "Darwin")
    assert not fda.available and "Full Disk Access" in fda.reason
    with pytest.raises(ImporterError, match="Full Disk Access"):
        list(IMessageImporter(db).iter_messages())


def test_imessage_bad_paths(tmp_path):
    with pytest.raises(ImporterError, match="not found"):
        list(IMessageImporter(tmp_path / "missing.db").iter_messages())
    (tmp_path / "junk.db").write_bytes(b"not sqlite at all")
    with pytest.raises(ImporterError, match="cannot open"):
        list(IMessageImporter(tmp_path / "junk.db").iter_messages())


def test_imessage_into_db(state, tmp_path):
    db = make_chat_db(tmp_path / "chat.db")
    report = run_import(IMessageImporter(db), DbSink(state))
    assert report.source.kind == "imessage" and report.source.label == "iMessage"
    assert report.received == 6 and report.inserted == 5 and report.skipped == 1
    assert report.skipped_reasons == {"url_only": 1}
    assert report.me_words == 7 + 4  # "not much, grinding for the exam tbh", "bu akşam gelir misin"
    assert report.conversations == 3
    s = corpus_db.stats(state.db)
    assert s.me_words == 11 and s.me_messages == 2 and s.other_messages == 3
    assert [r.kind for r in s.by_source] == ["imessage"]
    assert corpus_db.stats(state.db).by_register[0].register == "text"
