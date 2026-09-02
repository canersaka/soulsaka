from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from soulsaka.db import corpus as corpus_db
from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import ImporterError, run_import
from soulsaka.importers.sinks import DbSink
from soulsaka.importers.whatsapp import WhatsAppImporter, jid_to_handle
from soulsaka.importers.whatsapp_export import (
    WhatsAppExportImporter,
    build_datetime,
    classify,
    detect_date_order,
    parse_header,
)
from tests.fixtures.importers import (
    ALI_JID,
    ANDROID_EXPORT,
    GROUP_JID,
    IOS_EXPORT,
    PHONE_EXPORT,
    T0,
    make_export_zip,
    make_whatsapp_db,
)

ME = IdentityResolver(names=["Caner", "Caner Saka"])


# --- desktop database -------------------------------------------------------------------


def test_jid_to_handle():
    assert jid_to_handle("905321234567@s.whatsapp.net") == "+905321234567"
    assert jid_to_handle("123-456@g.us") is None
    assert jid_to_handle("status@broadcast") is None
    assert jid_to_handle("abc123@lid") == "abc123@lid"
    assert jid_to_handle(None) is None and jid_to_handle("") is None


def test_whatsapp_db_import(tmp_path):
    db = make_whatsapp_db(tmp_path / "ChatStorage.sqlite")
    msgs = list(WhatsAppImporter(db).iter_messages())
    # media (3), status (6) and empty (7) rows are skipped
    assert [m.external_id for m in msgs] == ["3A1", "3A2", "3A4", "3A5"]
    ali, me, veli, me_group = msgs
    assert not ali.is_me and ali.sender_handle == "+905321234567" and ali.sender_name == "Ali"
    assert ali.conversation_external_id == ALI_JID and ali.conversation_title == "Ali"
    assert not ali.is_group and ali.ts == T0 and ali.register == "text"
    assert me.is_me and me.sender_handle is None and me.text == "iyiyim sen nasılsın"
    assert veli.is_group and veli.conversation_title == "Trip"
    assert veli.conversation_external_id == GROUP_JID
    assert veli.sender_handle == "+905559998877" and veli.sender_name == "Veli"
    assert me_group.is_me and me_group.is_group and me_group.sender_name is None


def test_whatsapp_discover(tmp_path):
    home = tmp_path / "home"
    db = make_whatsapp_db(WhatsAppImporter.default_path(home))
    (found,) = WhatsAppImporter.discover(home, "Darwin")
    assert found.available and found.estimate == 6 and found.locator == str(db)
    (win,) = WhatsAppImporter.discover(home, "Windows")
    assert not win.available and "whatsapp-export" in win.reason
    (none,) = WhatsAppImporter.discover(tmp_path / "nothing", "Darwin")
    assert not none.available and "not found" in none.reason


def test_whatsapp_db_into_db(state, tmp_path):
    db = make_whatsapp_db(tmp_path / "ChatStorage.sqlite")
    report = run_import(WhatsAppImporter(db), DbSink(state))
    assert report.source.kind == "whatsapp" and report.inserted == 4
    assert report.me_words == 3 + 5 and report.conversations == 2
    assert corpus_db.stats(state.db).other_messages == 2


# --- text exports ------------------------------------------------------------------------


def test_parse_header_variants():
    ios = parse_header("[1/2/24, 3:45:12 PM] Caner: hey")
    assert (
        ios
        and ios.date == (1, 2, 24, "/")
        and ios.time == "3:45:12 PM"
        and ios.rest == "Caner: hey"
    )
    android = parse_header("1/2/24, 3:45 PM - Caner: hey")
    assert android and android.rest == "Caner: hey"
    turkish = parse_header("2.01.2024 15:45 - Ali Veli: selam")
    assert turkish and turkish.date == (2, 1, 2024, ".") and turkish.time == "15:45"
    narrow = parse_header("‎[1/2/24, 3:45:12 PM] Ali: x")
    assert narrow and narrow.time.endswith("PM")
    assert parse_header("continuation line") is None
    assert parse_header("") is None


def test_detect_date_order():
    assert detect_date_order([(1, 2, 24, "/"), (1, 13, 24, "/")]) == "mdy"
    assert detect_date_order([(13, 1, 24, "/"), (1, 2, 24, "/")]) == "dmy"
    assert detect_date_order([(1, 2, 24, "/")]) == "mdy"
    assert detect_date_order([(2, 1, 2024, ".")]) == "dmy"
    assert detect_date_order([(2024, 1, 2, "-")]) == "ymd"
    assert detect_date_order([]) == "mdy"


def test_build_datetime():
    assert build_datetime((1, 2, 24, "/"), "3:45:12 PM", "mdy") == datetime(2024, 1, 2, 15, 45, 12)
    assert build_datetime((13, 1, 2024, "."), "15:45", "dmy") == datetime(2024, 1, 13, 15, 45)
    assert build_datetime((2024, 1, 2, "-"), "12:05 AM", "ymd") == datetime(2024, 1, 2, 0, 5)
    assert build_datetime((1, 2, 24, "/"), "12:05 PM", "mdy") == datetime(2024, 1, 2, 12, 5)
    assert build_datetime((1, 2, 24, "/"), "3:45 ÖS", "mdy") == datetime(2024, 1, 2, 15, 45)
    with pytest.raises(ValueError):
        build_datetime((31, 2, 24, "/"), "3:45", "dmy")


def test_classify_system_and_media_lines():
    assert classify("Ali: hey") == ("Ali", "hey")
    assert classify("Ali: ‎image omitted") == ("Ali", "<Media omitted>")
    assert classify("Ali: ‎<attached: 0001-PHOTO.jpg>") == ("Ali", "<Media omitted>")
    assert classify("Ali: <Media omitted>") == ("Ali", "<Media omitted>")
    assert classify("Trip: ‎Ali created group “Trip”") is None
    assert classify("Trip: ‎Ali added Veli") is None
    assert classify("Messages and calls are end-to-end encrypted. No one outside") is None
    assert classify('Ali created group "Trip"') is None
    assert classify("Ali: Ali created group “Trip”") is None
    assert classify("Ali: I added milk to the list") == ("Ali", "I added milk to the list")
    assert classify("Ali: time: 5pm") == ("Ali", "time: 5pm")


def test_export_ios_english(tmp_path):
    path = tmp_path / "WhatsApp Chat - Trip.txt"
    path.write_text(IOS_EXPORT, encoding="utf-8")
    msgs = list(WhatsAppExportImporter(path, identity=ME).iter_messages())
    assert [(m.is_me, m.text) for m in msgs] == [
        (False, "hey are you coming tonight?"),
        (True, "yeah I think so\nwe could meet at 8"),
        (False, "<Media omitted>"),
        (False, "ok"),
        (True, "<Media omitted>"),
    ]
    assert msgs[0].ts == datetime(2024, 1, 2, 15, 46, tzinfo=UTC)
    assert msgs[3].ts == datetime(2024, 1, 13, 9, 5, tzinfo=UTC)
    assert msgs[0].sender_name == "Ali" and msgs[0].sender_handle is None
    assert msgs[1].sender_name is None and msgs[1].sender_handle is None
    assert {m.conversation_external_id for m in msgs} == {"WhatsApp Chat - Trip"}
    assert msgs[0].conversation_title == "Ali, Veli" and msgs[0].is_group
    assert all(m.register == "text" and m.external_id is None for m in msgs)


def test_export_android_turkish(tmp_path):
    path = tmp_path / "WhatsApp Chat with Ali Veli.txt"
    path.write_text(ANDROID_EXPORT, encoding="utf-8")
    msgs = list(WhatsAppExportImporter(path, identity=ME).iter_messages())
    assert [m.text for m in msgs] == [
        "bu akşam geliyor musun",
        "gelirim ya\nsaat 8 gibi orada olurum",
        "<Medya dahil edilmedi>",
        "tamam görüşürüz",
    ]
    assert [m.is_me for m in msgs] == [False, True, False, False]
    assert msgs[0].ts == datetime(2024, 1, 2, 15, 46, tzinfo=UTC)
    assert msgs[3].ts == datetime(2024, 1, 13, 9, 10, tzinfo=UTC)
    assert msgs[0].conversation_title == "Ali Veli" and not msgs[0].is_group
    assert msgs[0].conversation_external_id == "WhatsApp Chat with Ali Veli"


def test_export_zip_and_directory(tmp_path):
    folder = tmp_path / "exports"
    zip_path = make_export_zip(folder / "WhatsApp Chat - Trip.zip")
    (folder / "WhatsApp Chat with Ali Veli.txt").write_text(ANDROID_EXPORT, encoding="utf-8")
    (folder / ".DS_Store").write_bytes(b"\x00")
    from_zip = list(WhatsAppExportImporter(zip_path, identity=ME).iter_messages())
    assert len(from_zip) == 5 and from_zip[0].conversation_external_id == "WhatsApp Chat - Trip"
    everything = list(WhatsAppExportImporter(folder, identity=ME).iter_messages())
    assert len(everything) == 9
    assert {m.conversation_external_id for m in everything} == {
        "WhatsApp Chat - Trip",
        "WhatsApp Chat with Ali Veli",
    }
    with pytest.raises(ImporterError, match="not found"):
        list(WhatsAppExportImporter(tmp_path / "missing.txt", identity=ME).iter_messages())


def test_export_identity_handling(tmp_path):
    path = tmp_path / "WhatsApp Chat with Ali Veli.txt"
    path.write_text(ANDROID_EXPORT, encoding="utf-8")
    with pytest.raises(ImporterError, match="Ali Veli, Caner Saka"):
        list(WhatsAppExportImporter(path, identity=IdentityResolver()).iter_messages())
    nobody = WhatsAppExportImporter(path, identity=IdentityResolver(names=["Nobody"]))
    assert list(nobody.iter_messages()) == []
    assert len(nobody.notes) == 1 and "--me" in nobody.notes[0]
    with_me = WhatsAppExportImporter(path, identity=IdentityResolver(), me="caner  saka")
    assert [m.is_me for m in with_me.iter_messages()] == [False, True, False, False]


def test_export_phone_number_senders(tmp_path):
    path = tmp_path / "WhatsApp Chat with +90 532 000 00 00.txt"
    path.write_text(PHONE_EXPORT, encoding="utf-8")
    other, me = list(WhatsAppExportImporter(path, identity=ME).iter_messages())
    assert other.sender_handle == "+90 532 000 00 00" and other.sender_name is None
    assert not other.is_me and other.conversation_title == "+90 532 000 00 00"
    assert me.is_me
    mine = IdentityResolver(phones=["+905320000000"])
    assert [m.is_me for m in WhatsAppExportImporter(path, identity=mine).iter_messages()] == [
        True,
        False,
    ]


def test_export_discover(tmp_path):
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    (downloads / "WhatsApp Chat with Ali Veli.txt").write_text(ANDROID_EXPORT, encoding="utf-8")
    make_export_zip(downloads / "WhatsApp Chat - Trip.zip")
    (downloads / "notes.txt").write_text("not an export")
    unzipped = downloads / "WhatsApp Chat - Veli"
    unzipped.mkdir()
    (unzipped / "_chat.txt").write_text(IOS_EXPORT, encoding="utf-8")
    found = WhatsAppExportImporter.discover(home, "Darwin")
    assert sorted(Path(f.locator).name for f in found) == [
        "WhatsApp Chat - Trip.zip",
        "WhatsApp Chat - Veli",
        "WhatsApp Chat with Ali Veli.txt",
    ]
    by_suffix = {Path(f.locator).suffix: f for f in found}
    assert by_suffix[".txt"].estimate == 5 and by_suffix[".zip"].estimate is None
    assert all(f.kind == "whatsapp" and f.importer_kind == "whatsapp_export" for f in found)
    assert all(f.available and "~/Downloads" in f.label for f in found)
    msgs = list(WhatsAppExportImporter(unzipped, identity=ME).iter_messages())
    assert len(msgs) == 5 and msgs[0].conversation_external_id == "WhatsApp Chat - Veli"


def test_export_into_db(state, tmp_path):
    path = tmp_path / "WhatsApp Chat with Ali Veli.txt"
    path.write_text(ANDROID_EXPORT, encoding="utf-8")
    report = run_import(WhatsAppExportImporter(path, identity=ME), DbSink(state))
    assert report.source.kind == "whatsapp" and report.received == 4 and report.inserted == 3
    assert report.source.label == "WhatsApp export WhatsApp Chat with Ali Veli.txt"
    assert report.skipped_reasons == {"media_placeholder": 1}
    assert report.me_words == 7
    s = corpus_db.stats(state.db)
    assert s.me_words == 7 and s.other_messages == 2 and s.by_lang == {"tr": 7}
