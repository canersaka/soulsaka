"""Writing of mine: a folder of ``.md`` / ``.txt`` files (notes, essays, journals).

One message per file, registered as ``doc``; files over 1500 words are split at
paragraph boundaries into chunks of at most that size. The file's modification time is
the timestamp. One conversation per directory. YAML front matter and fenced code blocks
are removed so only prose remains. Everything here is *me* -- point it at your own
writing, not at a vendored docs folder.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from soulsaka.importers.base import Importer, ImporterError, register_importer
from soulsaka.models import ImportedMessage
from soulsaka.text.normalize import word_count

MAX_CHUNK_WORDS = 1500
MAX_FILE_BYTES = 5 * 1024 * 1024
SUFFIXES = {".md", ".txt", ".markdown"}
SKIP_DIRS = {"node_modules", ".venv", "venv", "site-packages", "__pycache__"}
_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def prose(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _FRONT_MATTER_RE.sub("", text)
    return _FENCE_RE.sub("", text).strip()


def chunk_paragraphs(text: str, max_words: int = MAX_CHUNK_WORDS) -> list[str]:
    """Whole text if short; otherwise paragraphs packed into chunks under ``max_words``."""
    if word_count(text) <= max_words:
        return [text] if text.strip() else []
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        words = word_count(paragraph)
        if current and current_words + words > max_words:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
        current.append(paragraph)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def iter_doc_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file() and path.suffix.lower() in SUFFIXES:
            yield path


@register_importer
class DocsImporter(Importer):
    kind = "docs"
    source_kind = "doc"
    register = "doc"
    label = "documents"

    def iter_messages(self) -> Iterator[ImportedMessage]:
        root = Path(self.locator).expanduser()
        if root.is_file():
            files, root = [root], root.parent
        elif root.is_dir():
            files = list(iter_doc_files(root))
        else:
            raise ImporterError(f"not found: {root}")
        for path in files:
            yield from self._iter_file(root, path)

    @staticmethod
    def _iter_file(root: Path, path: Path) -> Iterator[ImportedMessage]:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return
            text = prose(path.read_text(encoding="utf-8", errors="replace"))
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            return
        rel = path.relative_to(root)
        folder = rel.parent.as_posix()
        conversation = str(root / rel.parent)
        for i, chunk in enumerate(chunk_paragraphs(text)):
            yield ImportedMessage(
                conversation_external_id=conversation,
                text=chunk,
                ts=ts,
                is_me=True,
                register="doc",
                external_id=f"{rel.as_posix()}#{i}",
                conversation_title=root.name if folder == "." else folder,
                is_group=False,
                meta={"file": rel.as_posix(), "chunk": i},
            )
