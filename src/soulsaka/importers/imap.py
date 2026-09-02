"""IMAP: pull sent mail straight from the server (Gmail, iCloud, Fastmail, Exchange...).

The only importer that needs credentials. Gmail refuses account passwords over IMAP:
create an app password (Google Account → Security → 2-Step Verification → App
passwords) and enable IMAP in Gmail settings. The password is taken from ``--password``,
the ``SOULSAKA_IMAP_PASSWORD`` environment variable, or an interactive prompt; it is never
stored.

Without ``--folder`` the folder flagged ``\\Sent`` by the server (or a common Sent name)
is imported and everything in it is *me*. Extra folders such as ``INBOX`` add context:
other people's messages are kept only when their thread has something I wrote, so list
Sent folders first.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import re
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date

from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import Importer, ImporterError, register_importer
from soulsaka.importers.mailclean import from_address, parse_mail, to_imported
from soulsaka.models import ImportedMessage

GMAIL_APP_PASSWORD_NOTE = (
    "Gmail needs an app password (Google Account → Security → 2-Step Verification → "
    "App passwords) and IMAP enabled in Gmail settings."
)
COMMON_SENT_FOLDERS = (
    "[Gmail]/Sent Mail",
    "Sent",
    "Sent Items",
    "Sent Messages",
    "INBOX.Sent",
    "INBOX/Sent",
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$')
_FETCH_BATCH = 50


def imap_date(day: date) -> str:
    """``2020-01-02`` -> ``02-Jan-2020`` (IMAP wants English month abbreviations)."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def quote_folder(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_list_line(line: bytes) -> tuple[set[str], str] | None:
    """``(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"`` -> ``({"\\sent", ...}, name)``."""
    m = _LIST_RE.match(line.strip())
    if not m:
        return None
    flags = {f.decode("ascii", "replace").lower() for f in m["flags"].split()}
    name = m["name"].decode("utf-8", "replace").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return flags, name


def is_sent_folder(name: str, flags: frozenset[str] | set[str] = frozenset()) -> bool:
    if "\\sent" in flags:
        return True
    leaf = re.split(r"[/.]", name)[-1].strip().casefold()
    return leaf.startswith("sent") or leaf in {"gönderilenler", "gönderilmiş öğeler"}


@register_importer
class ImapImporter(Importer):
    kind = "imap"
    source_kind = "email"
    register = "email"
    label = "IMAP"

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        folders: list[str] | None = None,
        since: date | None = None,
        port: int = 993,
        identity: IdentityResolver | None = None,
        connect: Callable[[], imaplib.IMAP4] | None = None,
    ) -> None:
        super().__init__(f"imap://{user}@{host}", identity=identity)
        self.host, self.user, self.password, self.port = host, user, password, port
        self.folders = list(folders or [])
        self.since = since
        self._connect = connect

    # -- connection -------------------------------------------------------------------
    def _open(self) -> imaplib.IMAP4:
        try:
            conn = self._connect() if self._connect else imaplib.IMAP4_SSL(self.host, self.port)
        except OSError as e:
            raise ImporterError(f"cannot connect to {self.host}:{self.port}: {e}") from e
        try:
            conn.login(self.user, self.password)
        except imaplib.IMAP4.error as e:
            hint = f" {GMAIL_APP_PASSWORD_NOTE}" if "gmail" in self.host.lower() else ""
            raise ImporterError(f"login failed for {self.user}@{self.host}: {e}.{hint}") from e
        return conn

    @staticmethod
    def list_folders(conn: imaplib.IMAP4) -> list[tuple[set[str], str]]:
        status, lines = conn.list()
        if status != "OK":
            return []
        out: list[tuple[set[str], str]] = []
        for line in lines:
            if isinstance(line, bytes):
                parsed = parse_list_line(line)
                if parsed:
                    out.append(parsed)
        return out

    def find_sent_folder(self, conn: imaplib.IMAP4) -> str:
        folders = self.list_folders(conn)
        for flags, name in folders:
            if "\\sent" in flags:
                return name
        names = {name for _flags, name in folders}
        for candidate in COMMON_SENT_FOLDERS:
            if candidate in names:
                return candidate
        listing = ", ".join(sorted(names)) or "none"
        raise ImporterError(f"no Sent folder found; pass --folder. Folders: {listing}")

    # -- streaming --------------------------------------------------------------------
    def iter_messages(self) -> Iterator[ImportedMessage]:
        conn = self._open()
        skipped: Counter[str] = Counter()
        me_threads: set[str] = set()
        try:
            folders = self.folders or [self.find_sent_folder(conn)]
            flags_by_name = {name: flags for flags, name in self.list_folders(conn)}
            for folder in folders:
                in_sent = is_sent_folder(folder, flags_by_name.get(folder, set()))
                yield from self._iter_folder(conn, folder, in_sent, me_threads, skipped)
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
        for reason, n in sorted(skipped.items()):
            self.note(f"skipped {n} messages ({reason})")

    def _iter_folder(
        self,
        conn: imaplib.IMAP4,
        folder: str,
        in_sent: bool,
        me_threads: set[str],
        skipped: Counter[str],
    ) -> Iterator[ImportedMessage]:
        status, _ = conn.select(quote_folder(folder), readonly=True)
        if status != "OK":
            raise ImporterError(f"cannot open folder {folder!r}")
        criteria = ["SINCE", imap_date(self.since)] if self.since else ["ALL"]
        status, data = conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise ImporterError(f"search failed in {folder!r}")
        uids = data[0].split() if data and data[0] else []
        for start in range(0, len(uids), _FETCH_BATCH):
            batch = b",".join(uids[start : start + _FETCH_BATCH]).decode("ascii")
            status, items = conn.uid("FETCH", batch, "(BODY.PEEK[])")
            if status != "OK":
                raise ImporterError(f"fetch failed in {folder!r}")
            for item in items:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                yield from self._convert(item[1], in_sent, me_threads, skipped)

    def _convert(
        self, raw: bytes, in_sent: bool, me_threads: set[str], skipped: Counter[str]
    ) -> Iterator[ImportedMessage]:
        msg = email.message_from_bytes(raw)
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
