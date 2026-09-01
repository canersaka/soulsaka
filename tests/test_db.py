from __future__ import annotations

import threading

import pytest

from soulsaka.db import Database


def test_migrations_apply_once(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.migrations_run  # first open applies
    db.close()
    db2 = Database(tmp_path / "t.db")
    assert db2.migrations_run == []
    assert db2.scalar("SELECT COUNT(*) FROM schema_migrations") >= 1
    db2.close()


def test_tx_rollback(tmp_path):
    db = Database(tmp_path / "t.db")
    with pytest.raises(RuntimeError), db.tx() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES ('a', '1')")
        raise RuntimeError("boom")
    assert db.get_setting("a") is None
    db.set_setting("a", "2")
    assert db.get_setting("a") == "2"
    db.close()


def test_fts_triggers(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.tx() as conn:
        conn.execute("INSERT INTO sources(kind, label, created_at) VALUES ('x', 'x', 'now')")
        conn.execute("INSERT INTO conversations(source_id, external_id) VALUES (1, 'c')")
        conn.execute(
            "INSERT INTO messages(conversation_id, is_me, ts, register, text, word_count, content_hash) VALUES (1, 1, 't', 'text', 'hello wonderful world', 3, 'h')"
        )
    assert db.scalar("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'wonderful'") == 1
    with db.tx() as conn:
        conn.execute("UPDATE messages SET text = 'goodbye' WHERE id = 1")
    assert db.scalar("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'wonderful'") == 0
    assert db.scalar("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'goodbye'") == 1
    db.close()


def test_threads_get_own_connections(tmp_path):
    db = Database(tmp_path / "t.db")
    seen = []

    def work(i):
        db.set_setting(f"k{i}", str(i))
        seen.append(id(db.conn))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen)) == 4
    assert db.scalar("SELECT COUNT(*) FROM settings WHERE key LIKE 'k%'") == 4
    db.close()
