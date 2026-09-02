from __future__ import annotations

from datetime import UTC, datetime

import pytest

from soulsaka.db import corpus as corpus_db
from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import ImporterError, run_import
from soulsaka.importers.docs import DocsImporter, chunk_paragraphs, prose
from soulsaka.importers.git import GitImporter, clean_commit_message, find_repos
from soulsaka.importers.sinks import DbSink
from soulsaka.text.normalize import word_count
from tests.fixtures.importers import ME_EMAIL, make_docs_dir, make_git_repo

ME = IdentityResolver(emails=[ME_EMAIL])


# --- git ---------------------------------------------------------------------------------


def test_git_import(tmp_path):
    repo = make_git_repo(tmp_path / "proj")
    importer = GitImporter(roots=[tmp_path], identity=ME)
    msgs = list(importer.iter_messages())
    # newest first; "Initial commit", the merge-style subject and the other author are gone
    assert [m.text for m in msgs] == [
        "Fix typo in the setup guide",
        "Add importer skeleton\n\nStreams messages in chunks so mailboxes never sit in memory.",
    ]
    assert all(m.is_me and m.register == "doc" and m.conversation_title == "proj" for m in msgs)
    assert msgs[0].conversation_external_id == str(repo)
    assert msgs[0].ts == datetime(2024, 1, 2, 12, 45, 12, tzinfo=UTC)
    assert len(msgs[0].external_id) == 40
    ref = importer.source_ref()
    assert ref.kind == "git" and ref.locator == str(tmp_path)
    assert importer.notes == []


def test_git_find_repos_depth_and_skips(tmp_path):
    for rel in ("a/b/c/repo", "node_modules/dep", "a/b/c/d/e/deep", "Library/x/repo", ".hidden/r"):
        (tmp_path / rel / ".git").mkdir(parents=True)
    assert find_repos([tmp_path]) == [tmp_path / "a" / "b" / "c" / "repo"]
    assert find_repos([tmp_path], max_depth=6) == [
        tmp_path / "a" / "b" / "c" / "d" / "e" / "deep",
        tmp_path / "a" / "b" / "c" / "repo",
    ]
    assert find_repos([tmp_path / "missing"]) == []


def test_clean_commit_message():
    assert clean_commit_message("Merge pull request #1 from x/y") is None
    assert clean_commit_message("Initial commit") is None
    assert clean_commit_message("Bump version to 1.2.3") is None
    assert clean_commit_message("1.2.3") is None
    assert clean_commit_message("Update README.md") is None
    assert clean_commit_message("chore(deps): bump lodash") is None
    assert (
        clean_commit_message("Fix the thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        is None
    )
    assert clean_commit_message("   \n") is None
    assert (
        clean_commit_message(
            "Fix the thing\n\nBecause it was broken.\n\nSigned-off-by: Me <me@x>\nCo-authored-by: Ali <ali@x>\n"
        )
        == "Fix the thing\n\nBecause it was broken."
    )


def test_git_unavailable_or_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr("soulsaka.importers.git.global_git_email", lambda: None)
    importer = GitImporter(roots=[tmp_path], identity=IdentityResolver())
    assert list(importer.iter_messages()) == [] and "no email" in importer.notes[0]
    monkeypatch.setattr("soulsaka.importers.git.git_available", lambda: False)
    importer = GitImporter(roots=[tmp_path], identity=ME)
    assert list(importer.iter_messages()) == [] and "not installed" in importer.notes[0]
    (found,) = GitImporter.discover(tmp_path, "Linux")
    assert not found.available and "not installed" in found.reason


def test_git_global_email_fallback(tmp_path, monkeypatch):
    make_git_repo(tmp_path / "proj")
    monkeypatch.setattr("soulsaka.importers.git.global_git_email", lambda: ME_EMAIL)
    msgs = list(GitImporter(roots=[tmp_path], identity=IdentityResolver()).iter_messages())
    assert len(msgs) == 2


def test_git_discover(tmp_path):
    home = tmp_path / "home"
    (home / "code" / "proj" / ".git").mkdir(parents=True)
    (found,) = GitImporter.discover(home, "Darwin")
    assert found.available and "1 repositories" in found.label and found.kind == "git"
    (none,) = GitImporter.discover(tmp_path / "empty", "Darwin")
    assert not none.available and "no git repositories" in none.reason


# --- docs --------------------------------------------------------------------------------


def test_prose_and_chunking():
    assert prose("---\ntitle: x\n---\nBody\n\n```py\ncode\n```\n\nMore") == "Body\n\nMore"
    assert chunk_paragraphs("short text") == ["short text"]
    assert chunk_paragraphs("") == []
    big = "\n\n".join(" ".join(["w"] * 400) for _ in range(5))
    assert [word_count(c) for c in chunk_paragraphs(big, max_words=1000)] == [800, 800, 400]


def test_docs_import(tmp_path):
    root = make_docs_dir(tmp_path / "notes")
    msgs = list(DocsImporter(root).iter_messages())
    assert [m.external_id for m in msgs] == [
        "essay.md#0",
        "journal/2024.txt#0",
        "journal/2024.txt#1",
    ]
    essay = msgs[0]
    assert essay.text == "# Why I write\n\nBecause it helps me think.\n\nSecond paragraph of prose."
    assert essay.is_me and essay.register == "doc" and essay.conversation_title == "notes"
    assert essay.conversation_external_id == str(root)
    assert essay.ts.tzinfo is UTC and essay.meta == {"file": "essay.md", "chunk": 0}
    journal = msgs[1]
    assert journal.conversation_title == "journal"
    assert journal.conversation_external_id == str(root / "journal")
    assert word_count(msgs[1].text) == 1500 and word_count(msgs[2].text) == 500

    single = tmp_path / "one.md"
    single.write_text("just one file", encoding="utf-8")
    (m,) = DocsImporter(single).iter_messages()
    assert m.text == "just one file" and m.conversation_title == tmp_path.name
    with pytest.raises(ImporterError, match="not found"):
        list(DocsImporter(tmp_path / "missing").iter_messages())


def test_git_and_docs_into_db(state, tmp_path):
    make_git_repo(tmp_path / "proj")
    root = make_docs_dir(tmp_path / "notes")
    sink = DbSink(state)
    r1 = run_import(GitImporter(roots=[tmp_path / "proj"], identity=ME), sink)
    r2 = run_import(DocsImporter(root), sink)
    assert r1.inserted == 2 and r2.inserted == 3
    s = corpus_db.stats(state.db)
    assert [r.register for r in s.by_register] == ["doc"]
    assert s.me_words == r1.me_words + r2.me_words
    assert {r.kind for r in s.by_source} == {"git", "doc"}
