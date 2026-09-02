"""Commit messages I wrote, from every git repository under the given roots.

Roots (default: the home directory) are walked four levels deep, skipping dependency
and system folders; a directory holding ``.git`` is a repository and is not descended
into. ``git log`` is asked for commits whose author matches any identity email (or, if
none is configured, ``git config --global user.email``), one record per commit
separated by ASCII 0x1e with hash, author date and raw body split by 0x1f.

Merge commits, version bumps, bot commits and other boilerplate are skipped and trailer
lines (``Signed-off-by:``, ``Co-authored-by:`` ...) are dropped, leaving the prose.
Commit messages are registered as ``doc``; one conversation per repository.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import DiscoveredSource, Importer, register_importer
from soulsaka.models import ImportedMessage

MAX_DEPTH = 4
SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", ".tox", "site-packages", "__pycache__",
    "Library", "Applications", "Music", "Movies", "Pictures", "Photos", "AppData",
    ".Trash", ".cache", ".npm", ".cargo", ".rustup", ".pyenv", ".nvm", ".local", "go",
    "target", "build", "dist", ".gradle", ".m2",
}  # fmt: skip
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_AUTOMATED_RE = re.compile(
    r"^(?:merge\b|initial commit\b|bump version|bump \S+ from|release v?\d|v?\d+\.\d+(?:\.\d+)?$"
    r"|update (?:readme(?:\.md)?|\S+\.md)$|apply suggestions from code review|revert \"|chore\(deps"
    r"|\[skip ci\]|wip$)",
    re.IGNORECASE,
)
_BOT_RE = re.compile(
    r"co-authored-by:.*(?:claude|copilot|\[bot\]|anthropic|openai|dependabot)|generated with \[?claude|🤖 generated",
    re.IGNORECASE,
)
_TRAILER_RE = re.compile(
    r"^(?:signed-off-by|co-authored-by|change-id|reviewed-by|acked-by|tested-by|cc|fixes|closes"
    r"|resolves|refs?|see-also|claude-session|ticket|jira):\s",
    re.IGNORECASE,
)


def git_available() -> bool:
    return shutil.which("git") is not None


def find_repos(roots: Iterable[Path], max_depth: int = MAX_DEPTH) -> list[Path]:
    """Directories containing ``.git`` under the roots, at most ``max_depth`` levels down."""
    repos: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if (directory / ".git").exists():
            repos.append(directory)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name.startswith(".") and entry.name not in (".config", ".dotfiles"):
                continue
            walk(Path(entry.path), depth + 1)

    for root in roots:
        if root.is_dir():
            walk(root, 0)
    return repos


def global_git_email() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def clean_commit_message(body: str) -> str | None:
    """Drop trailers and boilerplate; None when nothing human is left."""
    if _BOT_RE.search(body):
        return None
    lines = [line.rstrip() for line in body.strip().split("\n")]
    if not lines or _AUTOMATED_RE.match(lines[0].strip()):
        return None
    kept = [line for line in lines if not _TRAILER_RE.match(line.strip())]
    text = "\n".join(kept).strip()
    return text or None


def read_log(repo: Path, emails: list[str]) -> Iterator[tuple[str, datetime, str]]:
    """``(hash, author date UTC, raw body)`` for commits by any of ``emails``."""
    cmd = ["git", "-C", str(repo), "log", "--no-merges", "--all"]
    cmd += [f"--author={email}" for email in emails]
    cmd.append(f"--format=%H{_FIELD_SEP}%aI{_FIELD_SEP}%B{_RECORD_SEP}")
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    for record in out.stdout.split(_RECORD_SEP):
        parts = record.strip("\n").split(_FIELD_SEP, 2)
        if len(parts) != 3:
            continue
        sha, when, body = parts
        try:
            ts = datetime.fromisoformat(when.strip()).astimezone(UTC)
        except ValueError:
            continue
        yield sha.strip(), ts, body


@register_importer
class GitImporter(Importer):
    kind = "git"
    register = "doc"
    label = "git commits"

    def __init__(
        self,
        locator: str | Path = "",
        *,
        identity: IdentityResolver | None = None,
        roots: list[Path] | None = None,
    ) -> None:
        self.roots = [Path(r).expanduser() for r in (roots or ([locator] if locator else []))]
        if not self.roots:
            self.roots = [Path.home()]
        super().__init__(os.pathsep.join(str(r) for r in self.roots), identity=identity)

    def emails(self) -> list[str]:
        emails = [e for e in self.identity.emails if e.strip()]
        if not emails:
            fallback = global_git_email()
            emails = [fallback] if fallback else []
        return emails

    def iter_messages(self) -> Iterator[ImportedMessage]:
        if not git_available():
            self.note("git is not installed; nothing imported")
            return
        emails = self.emails()
        if not emails:
            self.note("no email configured (me.emails) and no global git user.email; skipped")
            return
        for repo in find_repos(self.roots):
            yield from self._iter_repo(repo, emails)

    @staticmethod
    def _iter_repo(repo: Path, emails: list[str]) -> Iterator[ImportedMessage]:
        for sha, ts, body in read_log(repo, emails):
            text = clean_commit_message(body)
            if not text:
                continue
            yield ImportedMessage(
                conversation_external_id=str(repo),
                text=text,
                ts=ts,
                is_me=True,
                register="doc",
                external_id=sha,
                conversation_title=repo.name,
                is_group=False,
            )

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        if not git_available():
            return [cls.found(home, available=False, reason="git is not installed")]
        repos = find_repos([home])
        if not repos:
            return [cls.found(home, available=False, reason="no git repositories under ~")]
        return [cls.found(home, label=f"git commits in {len(repos)} repositories under ~")]
