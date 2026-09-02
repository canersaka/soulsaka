from __future__ import annotations

from tests.fixtures.importers import (
    ANDROID_EXPORT,
    IOS_EXPORT,
    make_discord_package,
    make_discord_zip,
    make_export_zip,
)


def _upload(client, path, **form):
    with path.open("rb") as fh:
        return client.post(
            "/api/import/upload",
            data=form,
            files={"file": (path.name, fh, "application/octet-stream")},
        )


def test_upload_whatsapp_zip_autodetected_and_idempotent(client, tmp_path):
    zip_path = make_export_zip(tmp_path / "WhatsApp Chat - Trip.zip", IOS_EXPORT)
    r = _upload(client, zip_path, kind="auto")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["source"]["kind"] == "whatsapp"
    assert (
        report["inserted"] >= 3 and report["me_words"] >= 6
    )  # "yeah I think so / we could meet at 8"
    again = _upload(client, zip_path, kind="auto").json()
    assert again["inserted"] == 0 and again["duplicates"] == report["inserted"]
    stats = client.get("/api/stats").json()
    assert stats["me_words"] == report["me_words"]
    sources = client.get("/api/sources").json()
    assert len(sources) == 1 and sources[0]["locator"].endswith(".zip")


def test_upload_android_txt_with_me_override(client, tmp_path):
    txt = tmp_path / "WhatsApp Chat with Ali Veli.txt"
    txt.write_text(ANDROID_EXPORT, encoding="utf-8")
    r = _upload(client, txt, kind="whatsapp_export", me="Caner Saka")
    assert r.status_code == 200, r.text
    assert r.json()["me_words"] >= 5


def test_upload_discord_package_zip(client, tmp_path):
    pkg = make_discord_package(tmp_path / "package")
    zip_path = make_discord_zip(tmp_path / "discord-package.zip", pkg)
    r = _upload(client, zip_path, kind="auto")
    assert r.status_code == 200, r.text
    assert r.json()["source"]["kind"] == "discord" and r.json()["inserted"] > 0


def test_upload_rejects_unknown_and_empty(client, tmp_path):
    junk = tmp_path / "photo.bin"
    junk.write_bytes(b"\x00\x01\x02 not a chat export")
    assert _upload(client, junk, kind="auto").status_code == 400
    assert _upload(client, junk, kind="nonsense").status_code == 400
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert _upload(client, empty).status_code == 400
    assert "whatsapp_export" in client.get("/api/import/kinds").json()
