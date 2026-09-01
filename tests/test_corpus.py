from __future__ import annotations

from datetime import UTC, datetime

from soulsaka.db import corpus as corpus_db
from soulsaka.models import ImportedMessage, SourceRef


def _msgs():
    t0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    return [
        ImportedMessage(
            conversation_external_id="c1",
            text="hey what's up",
            ts=t0,
            is_me=False,
            sender_handle="+16175550199",
            sender_name="Ali",
        ),
        ImportedMessage(
            conversation_external_id="c1",
            text="not much, grinding for the exam tbh",
            ts=t0.replace(minute=1),
            is_me=True,
        ),
        ImportedMessage(
            conversation_external_id="c1",
            text="<Media omitted>",
            ts=t0.replace(minute=2),
            is_me=True,
        ),
        ImportedMessage(
            conversation_external_id="c1",
            text="bu akşam gelir misin",
            ts=t0.replace(minute=3),
            is_me=False,
            sender_handle="+16175550199",
        ),
        ImportedMessage(
            conversation_external_id="c1",
            text="gelirim ya, saat 8 gibi",
            ts=t0.replace(minute=4),
            is_me=True,
        ),
    ]


def test_ingest_dedup_and_stats(state):
    src = SourceRef(kind="whatsapp", label="WhatsApp", locator="/tmp/x.txt")
    r1 = corpus_db.ingest_messages(state.db, state.salt, src, _msgs())
    assert r1.received == 5
    assert r1.inserted == 4
    assert r1.skipped == 1 and r1.skipped_reasons == {"media_placeholder": 1}
    assert r1.me_words == 7 + 5  # "not much, grinding for the exam tbh" + "gelirim ya, saat 8 gibi"
    r2 = corpus_db.ingest_messages(state.db, state.salt, src, _msgs())
    assert r2.inserted == 0 and r2.duplicates == 4

    s = corpus_db.stats(state.db)
    assert s.me_messages == 2 and s.other_messages == 2
    assert s.me_words == 12
    assert s.by_lang == {"tr": 5, "en": 7}
    assert s.conversations == 1
    assert not s.ready_for_first_train

    sources = corpus_db.list_sources(state.db)
    assert len(sources) == 1 and sources[0].me_messages == 2

    # other people's handles are hashed; only display names are kept
    rows = state.db.all("SELECT handle_hash, display_name, is_me FROM contacts ORDER BY id")
    assert any(r["is_me"] == 1 for r in rows)
    other = [r for r in rows if r["is_me"] == 0][0]
    assert other["display_name"] == "Ali" and "617" not in other["handle_hash"]


def test_search_messages(state):
    src = SourceRef(kind="whatsapp", label="WhatsApp", locator="x")
    corpus_db.ingest_messages(state.db, state.salt, src, _msgs())
    hits = corpus_db.search_messages(state.db, "exam")
    assert len(hits) == 1 and "exam" in hits[0].text
    assert corpus_db.search_messages(state.db, "akşam", me_only=False)
