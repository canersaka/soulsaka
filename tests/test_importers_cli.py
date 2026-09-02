from __future__ import annotations

import imaplib

import pytest
from typer.testing import CliRunner

from soulsaka.cli import app
from tests.fixtures.importers import ME_EMAIL, SENT_EMLX, FakeIMAP, make_mac_home

runner = CliRunner()


@pytest.fixture
def mac_home(data_dir, tmp_path, monkeypatch):
    home = make_mac_home(tmp_path / "home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("soulsaka.importers.discover.platform.system", lambda: "Darwin")
    return home


def test_import_without_auto_shows_help():
    result = runner.invoke(app, ["import"])
    assert result.exit_code == 2 and "Usage" in result.output
    result = runner.invoke(app, ["import", "--dry-run"])
    assert result.exit_code == 0 and "Usage" in result.output


def test_auto_dry_run_lists_sources(mac_home):
    result = runner.invoke(app, ["import", "--auto", "--dry-run"])
    assert result.exit_code == 0, result.output
    for label in ("iMessage", "WhatsApp", "Apple Mail", "Discord", "git commits"):
        assert label in result.output
    assert "ready" in result.output
    assert "words of you" not in result.output
    assert "soulsaka import imap" not in result.output  # Apple Mail and the mbox provide email


def test_auto_imports_everything_locally(mac_home):
    result = runner.invoke(app, ["import", "auto", "--yes", "--local"])
    assert result.exit_code == 0, result.output
    assert "local database" in result.output
    assert "words of you in the corpus" in result.output
    stats = runner.invoke(app, ["stats"])
    assert stats.exit_code == 0, stats.output
    for kind in ("imessage", "whatsapp", "email", "discord", "git"):
        assert kind in stats.output
    again = runner.invoke(app, ["import", "auto", "--yes", "--local"])
    assert again.exit_code == 0, again.output


def test_auto_hints_at_imap_when_no_email(data_dir, tmp_path, monkeypatch):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("soulsaka.importers.discover.platform.system", lambda: "Linux")
    result = runner.invoke(app, ["import", "--auto", "--dry-run"])
    assert result.exit_code == 1
    assert "soulsaka import imap" in result.output and "nothing readable" in result.output


def test_single_source_commands(mac_home, tmp_path):
    docs = mac_home / "Documents" / "notes"
    result = runner.invoke(app, ["import", "docs", str(docs), "--local"])
    assert result.exit_code == 0, result.output
    assert "documents" in result.output and "words of you" in result.output

    export = mac_home / "Downloads" / "WhatsApp Chat - Trip.zip"
    result = runner.invoke(
        app, ["import", "whatsapp-export", str(export), "--local", "--me", "Caner"]
    )
    assert result.exit_code == 0, result.output
    assert "WhatsApp export" in result.output

    result = runner.invoke(app, ["import", "git", str(mac_home / "code"), "--local"])
    assert result.exit_code == 0, result.output
    assert "commits" in result.output  # rich may wrap the label at 80 columns

    result = runner.invoke(app, ["import", "imessage", str(tmp_path / "missing.db"), "--local"])
    assert result.exit_code == 1 and "not found" in result.output


def test_imap_command_with_fake_server(data_dir, monkeypatch):
    fake = FakeIMAP({"[Gmail]/Sent Mail": [SENT_EMLX]})
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port: fake)
    monkeypatch.setenv("SOULSAKA_IMAP_PASSWORD", "app-pw")
    result = runner.invoke(
        app, ["import", "imap", "--host", "imap.gmail.com", "--user", ME_EMAIL, "--local"]
    )
    assert result.exit_code == 0, result.output
    assert "app password" in result.output and "words of you" in result.output
    assert fake.logged_out
