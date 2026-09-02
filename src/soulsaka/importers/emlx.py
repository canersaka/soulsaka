"""Apple Mail: ``~/Library/Mail/V*/<account>/<Mailbox>.mbox/.../Messages/<id>.emlx``.

Format notes
------------
Apple Mail stores one file per message. An ``.emlx`` file is: a first line holding the
byte length of the message, then exactly that many bytes of RFC 822 message, then an
XML plist with Mail's own flags. ``.partial.emlx`` files are the same with large
attachments split out into sibling files. Mailboxes are directories named
``<name>.mbox`` nested under a versioned root (``V10`` on recent macOS); Sent mail lives
in ``Sent Messages.mbox``, ``Sent.mbox`` or ``Sent Items.mbox`` depending on the account
type, and Gmail accounts add ``[Gmail].mbox/Sent Mail.mbox``. Reading the Mail folder
needs Full Disk Access.

Everything in a Sent mailbox is *me* (the From address is checked too); messages in
INBOX / Archive are context and only kept when their thread holds something I wrote.
"""

from __future__ import annotations

import email
from collections import Counter
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

from soulsaka.importers.base import (
    FULL_DISK_ACCESS_HINT,
    DiscoveredSource,
    Importer,
    ImporterError,
    display_path,
    register_importer,
)
from soulsaka.importers.mailclean import from_address, parse_mail, to_imported
from soulsaka.models import ImportedMessage

_SENT_NAMES = {
    "sent messages", "sent", "sent items", "sent mail",
    "gönderilenler", "gönderilmiş öğeler", "gönderilmiş",
}  # fmt: skip
_CONTEXT_NAMES = {"inbox", "archive", "all mail", "gelen kutusu", "arşiv"}


def read_emlx(path: Path) -> bytes:
    """The RFC 822 bytes inside an ``.emlx`` file (length line, message, plist)."""
    data = path.read_bytes()
    newline = data.find(b"\n")
    if newline < 0:
        raise ValueError(f"{path}: not an emlx file")
    try:
        length = int(data[:newline].strip())
    except ValueError as e:
        raise ValueError(f"{path}: not an emlx file") from e
    return data[newline + 1 : newline + 1 + length]


def parse_emlx(path: Path) -> Message:
    return email.message_from_bytes(read_emlx(path))


def mailbox_names(path: Path) -> list[str]:
    """Names of the ``.mbox`` directories above ``path``, innermost first."""
    return [p.name[: -len(".mbox")].casefold() for p in path.parents if p.name.endswith(".mbox")]


def classify_mailbox(path: Path) -> str | None:
    """``"sent"``, ``"context"`` or None (drafts, junk, trash, custom folders)."""
    names = mailbox_names(path)
    if any(n in _SENT_NAMES or n.startswith("sent") for n in names):
        return "sent"
    if names and names[0] in _CONTEXT_NAMES:
        return "context"
    return None


@register_importer
class EmlxImporter(Importer):
    kind = "emlx"
    source_kind = "email"
    register = "email"
    label = "Apple Mail"

    @staticmethod
    def default_path(home: Path | None = None) -> Path:
        return (home or Path.home()) / "Library" / "Mail"

    @staticmethod
    def collect(root: Path) -> tuple[list[Path], list[Path]]:
        """``(sent files, context files)`` under a Mail root, an account or one mailbox."""
        if root.is_file():
            return ([root], []) if classify_mailbox(root) != "context" else ([], [root])
        sent: list[Path] = []
        context: list[Path] = []
        for path in sorted(root.rglob("*.emlx")):
            kind = classify_mailbox(path)
            if kind == "sent":
                sent.append(path)
            elif kind == "context":
                context.append(path)
        return sent, context

    def iter_messages(self) -> Iterator[ImportedMessage]:
        root = Path(self.locator)
        try:
            if not root.exists():
                raise ImporterError(f"not found: {root}")
            sent, context = self.collect(root)
        except PermissionError as e:
            raise ImporterError(f"cannot read {root}. {FULL_DISK_ACCESS_HINT}") from e
        skipped: Counter[str] = Counter()
        me_threads: set[str] = set()
        for path in sent:
            yield from self._convert(path, me_threads, skipped, in_sent=True)
        for path in context:
            yield from self._convert(path, me_threads, skipped, in_sent=False)
        for reason, n in sorted(skipped.items()):
            self.note(f"skipped {n} messages ({reason})")

    def _convert(
        self, path: Path, me_threads: set[str], skipped: Counter[str], *, in_sent: bool
    ) -> Iterator[ImportedMessage]:
        try:
            msg = parse_emlx(path)
        except (OSError, ValueError):
            skipped["unreadable"] += 1
            return
        _name, addr = from_address(msg)
        is_me = in_sent or self.identity.is_me_handle(addr)
        mail = parse_mail(msg)
        if is_me:
            me_threads.add(mail.thread)
        elif mail.thread not in me_threads or mail.automated:
            return
        reason = mail.skip_reason()
        if reason:
            skipped[reason] += 1
            return
        yield to_imported(mail, is_me=is_me)

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        root = cls.default_path(home)
        if system != "Darwin":
            return [cls.found(root, available=False, reason="only on macOS; run on the Mac")]
        try:
            if not root.is_dir():
                return [
                    cls.found(
                        root, available=False, reason=f"not found: {display_path(root, home)}"
                    )
                ]
            sent, context = cls.collect(root)
        except PermissionError:
            return [cls.found(root, available=False, reason=FULL_DISK_ACCESS_HINT)]
        if not sent:
            return [cls.found(root, available=False, reason="no Sent mailbox with messages")]
        return [
            cls.found(
                root,
                estimate=len(sent) + len(context),
                label=f"Apple Mail ({len(sent):,} sent, {len(context):,} inbox)",
            )
        ]
