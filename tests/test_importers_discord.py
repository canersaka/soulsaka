from __future__ import annotations

import zipfile
from datetime import UTC, datetime

import pytest

from soulsaka.importers.base import ImporterError, run_import
from soulsaka.importers.discord import DiscordImporter, parse_timestamp
from soulsaka.importers.sinks import DbSink
from tests.fixtures.importers import make_discord_package, make_discord_zip


def test_parse_timestamp():
    assert parse_timestamp("2024-01-02 15:45:12.123000+00:00") == datetime(
        2024, 1, 2, 15, 45, 12, 123000, tzinfo=UTC
    )
    assert parse_timestamp("2024-01-02T15:47:00Z") == datetime(2024, 1, 2, 15, 47, tzinfo=UTC)
    assert parse_timestamp("2024-01-02 15:47:00") == datetime(2024, 1, 2, 15, 47, tzinfo=UTC)
    assert parse_timestamp("nope") is None and parse_timestamp("") is None


def test_discord_directory_and_zip(tmp_path):
    pkg = make_discord_package(tmp_path / "package")
    msgs = list(DiscordImporter(pkg).iter_messages())
    assert [(m.conversation_external_id, m.external_id, m.text) for m in msgs] == [
        ("111", "1", "hey alice"),
        ("111", "3", "see you there"),
        ("222", "4", "old style csv row"),
        ("333", "5", "orphan channel"),
    ]
    assert all(m.is_me and m.register == "text" and m.sender_handle is None for m in msgs)
    assert msgs[0].ts == datetime(2024, 1, 2, 15, 45, 12, 123000, tzinfo=UTC)
    assert msgs[1].ts == datetime(2024, 1, 2, 15, 47, tzinfo=UTC)
    assert msgs[0].conversation_title == "Direct Message with alice" and not msgs[0].is_group
    assert msgs[2].conversation_title == "general in Some Server" and msgs[2].is_group
    assert msgs[3].conversation_title == "channel 333" and msgs[3].is_group

    zipped = make_discord_zip(tmp_path / "package.zip", pkg)
    assert [m.external_id for m in DiscordImporter(zipped).iter_messages()] == ["1", "3", "4", "5"]
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zf:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                zf.write(f, "package/" + f.relative_to(pkg).as_posix())
    assert len(list(DiscordImporter(nested).iter_messages())) == 4
    assert len(list(DiscordImporter(pkg / "messages").iter_messages())) == 4


def test_discord_errors(tmp_path):
    with pytest.raises(ImporterError, match="not found"):
        list(DiscordImporter(tmp_path / "nope").iter_messages())
    (tmp_path / "empty").mkdir()
    with pytest.raises(ImporterError, match="index.json"):
        list(DiscordImporter(tmp_path / "empty").iter_messages())
    with zipfile.ZipFile(tmp_path / "other.zip", "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    with pytest.raises(ImporterError, match="index.json"):
        list(DiscordImporter(tmp_path / "other.zip").iter_messages())


def test_discord_discover(tmp_path):
    home = tmp_path / "home"
    pkg = make_discord_package(home / "Downloads" / "package")
    zipped = make_discord_zip(home / "Downloads" / "package.zip", pkg)
    (home / "Downloads" / "other.zip").write_bytes(b"PK")
    (home / "Downloads" / "discord-broken.zip").write_bytes(b"PK\x03\x04junk")
    found = DiscordImporter.discover(home, "Darwin")
    assert sorted(f.locator for f in found) == sorted([str(pkg), str(zipped)])
    assert all(f.kind == "discord" and f.available and f.estimate is None for f in found)
    assert DiscordImporter.discover(tmp_path / "nowhere", "Windows") == []


def test_discord_into_db(state, tmp_path):
    pkg = make_discord_package(tmp_path / "package")
    report = run_import(DiscordImporter(pkg), DbSink(state))
    assert report.source.kind == "discord" and report.inserted == 4
    assert report.me_words == 2 + 3 + 4 + 2 and report.conversations == 3
