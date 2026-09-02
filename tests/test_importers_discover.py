from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from soulsaka.importers.base import IMPORTERS
from soulsaka.importers.discover import discover_all, has_email_source
from tests.fixtures.importers import make_chat_db, make_mac_home


def test_discover_all_on_a_mac(tmp_path):
    home = make_mac_home(tmp_path / "home")
    found = discover_all(home, "Darwin")
    assert [s.importer_kind for s in found] == [
        "imessage", "whatsapp", "whatsapp_export", "whatsapp_export", "emlx", "mbox", "discord", "git",
    ]  # fmt: skip
    by_kind: dict[str, list] = defaultdict(list)
    for s in found:
        by_kind[s.importer_kind].append(s)
    assert by_kind["imessage"][0].available and by_kind["imessage"][0].estimate == 7
    assert by_kind["whatsapp"][0].available and by_kind["whatsapp"][0].estimate == 6
    assert by_kind["emlx"][0].available and by_kind["emlx"][0].estimate == 4
    assert sorted(Path(s.locator).name for s in by_kind["whatsapp_export"]) == [
        "WhatsApp Chat - Trip.zip",
        "WhatsApp Chat with Ali Veli.txt",
    ]
    assert by_kind["mbox"][0].estimate == 7 and by_kind["mbox"][0].kind == "email"
    assert Path(by_kind["discord"][0].locator).name == "package"
    assert by_kind["git"][0].available and "1 repositories" in by_kind["git"][0].label
    assert all(s.available for s in found)
    assert has_email_source(found)


def test_discover_all_elsewhere(tmp_path):
    home = make_mac_home(tmp_path / "home")
    found = discover_all(home, "Linux")
    mac_only = {
        s.importer_kind: s for s in found if s.importer_kind in ("imessage", "whatsapp", "emlx")
    }
    assert set(mac_only) == {"imessage", "whatsapp", "emlx"}
    assert all(not s.available and "macOS" in s.reason for s in mac_only.values())
    assert has_email_source(found)  # the Takeout mbox still counts
    empty = discover_all(tmp_path / "empty", "Windows")
    assert not has_email_source(empty) and not any(s.available for s in empty)
    windows = {s.importer_kind: s for s in empty}
    assert "whatsapp-export" in windows["whatsapp"].reason


def test_discover_survives_a_broken_probe(tmp_path, monkeypatch):
    def boom(cls, home, system):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(IMPORTERS["imessage"], "discover", classmethod(boom))
    found = discover_all(tmp_path, "Darwin")
    (imessage,) = [s for s in found if s.importer_kind == "imessage"]
    assert not imessage.available and "kaboom" in imessage.reason
    assert any(s.importer_kind == "whatsapp" for s in found)


def test_discover_defaults_use_home_and_platform(tmp_path, monkeypatch):
    home = tmp_path / "home"
    make_chat_db(home / "Library" / "Messages" / "chat.db")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("soulsaka.importers.discover.platform.system", lambda: "Darwin")
    found = discover_all()
    (imessage,) = [s for s in found if s.importer_kind == "imessage"]
    assert imessage.available and imessage.locator == str(home / "Library" / "Messages" / "chat.db")
