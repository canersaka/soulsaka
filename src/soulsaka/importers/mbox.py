"""Gmail Takeout (and any other) ``.mbox`` file.

Format notes
------------
An mbox is messages concatenated, each introduced by a ``From sender date`` separator
line; body lines that would look like one are escaped as ``>From``. Gmail Takeout adds
``X-GM-THRID`` (thread id) and ``X-Gmail-Labels`` (``Inbox,Sent,Important,...``) headers,
which give exact threads and a second signal for "sent by me" beyond the From address.

The file is read twice, one message at a time (``mailbox.mbox`` keeps only offsets in
memory): the first pass finds the threads I took part in, the second yields my messages
plus other people's messages from those threads, skipping automated senders.
"""

from __future__ import annotations

import mailbox
from collections import Counter
from collections.abc import Iterator
from email.message import Message
from email.parser import BytesHeaderParser
from pathlib import Path

from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import (
    DiscoveredSource,
    Importer,
    ImporterError,
    display_path,
    find_paths,
    register_importer,
    search_dirs,
)
from soulsaka.importers.mailclean import (
    SENT_LABELS,
    from_address,
    gmail_labels,
    is_automated,
    parse_mail,
    thread_key,
    to_imported,
)
from soulsaka.models import ImportedMessage

_COUNT_LIMIT_BYTES = 256 * 1024 * 1024
_AVERAGE_MESSAGE_BYTES = 10_000


def _from_line_date(msg: Message) -> str | None:
    """The date on the ``From `` separator line, as a fallback for a missing Date header."""
    get_from = getattr(msg, "get_from", None)
    if get_from is None:
        return None
    parts = (get_from() or "").split(" ", 1)
    return parts[1] if len(parts) == 2 else None


@register_importer
class MboxImporter(Importer):
    kind = "mbox"
    source_kind = "email"
    register = "email"
    label = "mbox"

    def __init__(self, locator: str | Path, *, identity: IdentityResolver | None = None) -> None:
        super().__init__(locator, identity=identity)
        self.source_label = f"{self.label} {Path(locator).name}"

    def _is_me(self, msg: Message, addr: str) -> bool:
        return self.identity.is_me_handle(addr) or bool(gmail_labels(msg) & SENT_LABELS)

    def iter_messages(self) -> Iterator[ImportedMessage]:
        path = Path(self.locator)
        if not path.is_file():
            raise ImporterError(f"not found: {path}")
        box = mailbox.mbox(str(path), create=False)
        skipped: Counter[str] = Counter()
        try:
            me_threads = self._scan(box)
            for key in box.iterkeys():
                yield from self._convert(box.get_message(key), me_threads, skipped)
        finally:
            box.close()
        for reason, n in sorted(skipped.items()):
            self.note(f"skipped {n} messages ({reason})")

    def _scan(self, box: mailbox.mbox) -> set[str]:
        """Headers-only pass: the threads where I wrote something."""
        me_threads: set[str] = set()
        senders: Counter[str] = Counter()
        parser = BytesHeaderParser()
        for key in box.iterkeys():
            with box.get_file(key) as fh:
                hdr = parser.parse(fh)
            _name, addr = from_address(hdr)
            if self._is_me(hdr, addr):
                me_threads.add(thread_key(hdr))
            else:
                senders[addr] += 1
        if not me_threads:
            top = ", ".join(a for a, _ in senders.most_common(5))
            raise ImporterError(
                f"no message in {self.locator} is from you (senders seen: {top}). "
                "Pass --me you@example.com or set me.emails in config.toml"
            )
        return me_threads

    def _convert(
        self, msg: Message, me_threads: set[str], skipped: Counter[str]
    ) -> Iterator[ImportedMessage]:
        _name, addr = from_address(msg)
        is_me = self._is_me(msg, addr)
        if not is_me and (thread_key(msg) not in me_threads or is_automated(msg, addr)):
            return
        mail = parse_mail(msg, fallback_date=_from_line_date(msg))
        reason = mail.skip_reason()
        if reason:
            skipped[reason] += 1
            return
        yield to_imported(mail, is_me=is_me)

    # -- discovery ------------------------------------------------------------------
    @staticmethod
    def estimate(path: Path) -> int:
        size = path.stat().st_size
        if size > _COUNT_LIMIT_BYTES:
            return max(1, size // _AVERAGE_MESSAGE_BYTES)
        with path.open("rb") as fh:
            return sum(1 for line in fh if line.startswith(b"From "))

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        out: list[DiscoveredSource] = []
        for path in find_paths(
            search_dirs(home), lambda p: p.is_file() and p.suffix.lower() == ".mbox"
        ):
            try:
                estimate = cls.estimate(path)
            except OSError as e:
                out.append(cls.found(path, available=False, reason=str(e)))
                continue
            out.append(cls.found(path, estimate=estimate, label=f"mbox {display_path(path, home)}"))
        return out
