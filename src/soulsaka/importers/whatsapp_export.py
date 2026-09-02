"""WhatsApp ``.txt`` chat exports (Settings → Chats → Export chat).

Format notes
------------
One line per message, continuation lines for multi-line messages, and no timezone:

    iOS      [1/2/24, 3:45:12 PM] Ali: hey
    Android  1/2/24, 3:45 PM - Ali: hey
    24-hour  [02.01.2024, 15:45:12] Ali: hey        (Turkish, German, ... locales)

The phone's locale decides day/month order, the separator, 12/24-hour clock and whether
a narrow no-break space sits before AM/PM. iOS prefixes text it generated itself
(system notices, media placeholders) with U+200E LEFT-TO-RIGHT MARK; RTL locales use
U+200F. System lines (group created, "X added Y", the end-to-end encryption notice)
have no ``Name:`` part on Android and carry the actor or chat name as a pseudo sender
on iOS. Media show up as ``<Media omitted>``, ``image omitted`` or ``<attached: file>``;
those are yielded (normalised to ``<Media omitted>`` where needed) and skipped by the
database layer, which counts them.

Day/month order is decided per file from every date in it: a first field over 12 means
day-first, a second field over 12 means month-first; otherwise ``dd.mm.yyyy`` is taken
as day-first and slash dates as month-first (the US default). Times are naive; they are
stored as UTC unless ``tz`` is given.

Exports never say who "me" is: sender names are matched against the identity names (or
``--me``). A file where nobody matches is skipped with a note; when no name is
configured at all the import stops and lists the participants it saw.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
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
from soulsaka.models import ImportedMessage

_MARKS = "‎‏‪‫‬‭‮⁦⁧⁨⁩"
_MARKS_RE = re.compile(f"[{_MARKS}]")
_DATE = r"(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
_TIME = (
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:[\s  ]*(?:[AaPp]\.?\s?[Mm]\.?|ÖÖ|ÖS))?)"
)
_IOS_RE = re.compile(rf"^[{_MARKS}]*\[{_DATE},?\s+{_TIME}\]\s?(?P<rest>.*)$")
_ANDROID_RE = re.compile(rf"^[{_MARKS}]*{_DATE},?\s+{_TIME}\s[-–—]\s(?P<rest>.*)$")
_SENDER_RE = re.compile(r"^(?P<sender>[^:\n]{1,80}?):\s?(?P<body>.*)$", re.S)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp]|ÖÖ|ÖS)?")
_PHONE_RE = re.compile(r"^\+?[\d\s().\- ]{6,}$")
# iOS-generated (mark-prefixed) lines that stand in for media; kept so the database
# layer can count them as skipped placeholders rather than losing them silently.
_MEDIA_RE = re.compile(
    r"omitted|attached:|dahil edilmedi|deleted|silindi|missed|cevapsız|location:|konum:",
    re.IGNORECASE,
)
# Notices that can carry a sender prefix without a mark (older iOS exports).
_NOTICE_RE = re.compile(
    r"^(?:messages and calls are end-to-end encrypted|mesajlar ve aramalar uçtan uca şifreli"
    r"|your security code with|.{1,60}? (?:created group|grubu oluşturdu|changed the subject"
    r"|konuyu .{0,80}değiştirdi|changed this group's|changed the group description"
    r"|joined using this group's invite link|davet bağlantısı))",
    re.IGNORECASE,
)
_TITLE_PREFIXES = (
    "WhatsApp Chat with ",
    "WhatsApp Chat - ",
    "WhatsApp Sohbeti - ",
    "WhatsApp Chat ",
)
_EXPORT_NAME_RE = re.compile(r"^(WhatsApp (Chat|Sohbet)|_chat)", re.IGNORECASE)


@dataclass
class _Unit:
    """One chat: a ``.txt`` file, or a member of a ``.zip`` export."""

    conversation_id: str
    name: str
    lines: Callable[[], Iterator[str]]


@dataclass
class _Header:
    date: tuple[int, int, int, str]  # first, second, third field and separator
    time: str
    rest: str


def parse_header(line: str) -> _Header | None:
    """Match the ``[date, time] rest`` / ``date, time - rest`` prefix of a message line."""
    m = _IOS_RE.match(line) or _ANDROID_RE.match(line)
    if not m:
        return None
    date = m["date"]
    sep = "." if "." in date else ("/" if "/" in date else "-")
    try:
        a, b, c = (int(x) for x in date.split(sep))
    except ValueError:
        return None
    return _Header(date=(a, b, c, sep), time=m["time"], rest=m["rest"])


def detect_date_order(dates: Iterable[tuple[int, int, int, str]]) -> str:
    """``dmy`` / ``mdy`` / ``ymd`` from the whole file's dates (see module docstring)."""
    day_first = month_first = year_first = dotted = False
    for a, b, _c, sep in dates:
        year_first |= a >= 100
        day_first |= 12 < a < 100
        month_first |= b > 12
        dotted |= sep == "."
    if year_first:
        return "ymd"
    if day_first:
        return "dmy"
    if month_first:
        return "mdy"
    return "dmy" if dotted else "mdy"


def build_datetime(date: tuple[int, int, int, str], time: str, order: str) -> datetime:
    a, b, c, _sep = date
    if order == "ymd":
        year, month, day = a, b, c
    elif order == "dmy":
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    if year < 100:
        year += 2000
    m = _TIME_RE.match(time.replace(" ", " ").replace(" ", " "))
    if not m:
        raise ValueError(time)
    hour, minute, second = int(m[1]), int(m[2]), int(m[3] or 0)
    suffix = (m[4] or "").upper()
    if suffix in ("P", "ÖS") and hour < 12:
        hour += 12
    elif suffix in ("A", "ÖÖ") and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, second)


def classify(rest: str) -> tuple[str, str] | None:
    """``(sender, body)`` for a chat message, or None for a system line."""
    m = _SENDER_RE.match(rest)
    if not m:
        return None
    sender = _MARKS_RE.sub("", m["sender"]).strip()
    body = m["body"]
    marked = body.lstrip().startswith(("‎", "‏"))
    body = _MARKS_RE.sub("", body).strip()
    if not sender:
        return None
    if marked:
        return (sender, "<Media omitted>") if _MEDIA_RE.search(body) else None
    if _NOTICE_RE.match(body):
        return None
    return sender, body


def title_from_name(name: str) -> str:
    for prefix in _TITLE_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _file_lines(path: Path) -> Callable[[], Iterator[str]]:
    def lines() -> Iterator[str]:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            yield from fh

    return lines


def _zip_lines(path: Path, member: str) -> Callable[[], Iterator[str]]:
    def lines() -> Iterator[str]:
        with zipfile.ZipFile(path) as zf, zf.open(member) as raw:
            yield from io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")

    return lines


def _zip_units(path: Path) -> Iterator[_Unit]:
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".txt")]
    for member in sorted(members):
        stem = Path(member).stem
        name = path.stem if stem == "_chat" else stem
        yield _Unit(conversation_id=name, name=name, lines=_zip_lines(path, member))


def _file_unit(path: Path) -> _Unit:
    name = path.parent.name if path.stem == "_chat" else path.stem
    return _Unit(conversation_id=name, name=name, lines=_file_lines(path))


def iter_units(path: Path) -> Iterator[_Unit]:
    """A file, a zip, or a directory of either."""
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if any(part.startswith(".") for part in p.relative_to(path).parts):
                continue
            if p.suffix.lower() == ".txt":
                yield _file_unit(p)
            elif p.suffix.lower() == ".zip":
                yield from _zip_units(p)
    elif path.suffix.lower() == ".zip":
        yield from _zip_units(path)
    elif path.is_file():
        yield _file_unit(path)
    else:
        raise ImporterError(f"not found: {path}")


@register_importer
class WhatsAppExportImporter(Importer):
    kind = "whatsapp_export"
    source_kind = "whatsapp"
    register = "text"
    label = "WhatsApp export"

    def __init__(
        self,
        locator: str | Path,
        *,
        identity: IdentityResolver | None = None,
        me: str | None = None,
        tz: tzinfo = UTC,
    ) -> None:
        super().__init__(locator, identity=identity)
        if me:
            self.identity = IdentityResolver(
                names=[*self.identity.names, me],
                emails=list(self.identity.emails),
                phones=list(self.identity.phones),
            )
        self.tz = tz

    # -- identity -------------------------------------------------------------------
    def _is_me(self, sender: str) -> bool:
        phone = bool(_PHONE_RE.match(sender))
        return self.identity.is_me(handle=sender if phone else None, name=sender)

    # -- streaming ------------------------------------------------------------------
    def iter_messages(self) -> Iterator[ImportedMessage]:
        for unit in iter_units(Path(self.locator)):
            yield from self._iter_unit(unit)

    def _scan(self, unit: _Unit) -> tuple[str, list[str]]:
        """First pass: date order and participants, in order of first appearance."""
        dates: list[tuple[int, int, int, str]] = []
        participants: dict[str, None] = {}
        for line in unit.lines():
            header = parse_header(line.rstrip("\n"))
            if header is None:
                continue
            dates.append(header.date)
            parsed = classify(header.rest)
            if parsed is not None:
                participants.setdefault(parsed[0], None)
        return detect_date_order(dates), list(participants)

    def _iter_unit(self, unit: _Unit) -> Iterator[ImportedMessage]:
        order, participants = self._scan(unit)
        if not participants:
            self.note(f"{unit.name}: no messages recognised")
            return
        others = [p for p in participants if not self._is_me(p)]
        if len(others) == len(participants):
            seen = ", ".join(participants)
            if not (self.identity.names or self.identity.phones):
                raise ImporterError(
                    f"cannot tell which participant is you in {unit.name} (seen: {seen}). "
                    "Pass --me NAME or set me.names in config.toml"
                )
            self.note(f"skipped {unit.name}: none of {seen} matches your name (use --me NAME)")
            return
        title = ", ".join(others) if others else title_from_name(unit.name)
        is_group = len(others) > 1
        current: tuple[str, list[str], datetime] | None = None
        for line in unit.lines():
            line = line.rstrip("\n")
            header = parse_header(line)
            if header is None:
                if current is not None:
                    current[1].append(_MARKS_RE.sub("", line))
                continue
            if current is not None:
                yield self._emit(unit, title, is_group, *current)
                current = None
            parsed = classify(header.rest)
            if parsed is None:
                continue
            try:
                ts = build_datetime(header.date, header.time, order)
            except ValueError:
                self.note(f"{unit.name}: skipped a line with an unreadable date")
                continue
            current = (parsed[0], [parsed[1]], ts)
        if current is not None:
            yield self._emit(unit, title, is_group, *current)

    def _emit(
        self,
        unit: _Unit,
        title: str,
        is_group: bool,
        sender: str,
        body: list[str],
        ts: datetime,
    ) -> ImportedMessage:
        phone = bool(_PHONE_RE.match(sender))
        is_me = self._is_me(sender)
        return ImportedMessage(
            conversation_external_id=unit.conversation_id,
            text="\n".join(body),
            ts=ts.replace(tzinfo=self.tz),
            is_me=is_me,
            register="text",
            conversation_title=title,
            is_group=is_group,
            sender_handle=None if is_me or not phone else sender,
            sender_name=None if is_me or phone else sender,
        )

    # -- discovery ------------------------------------------------------------------
    @staticmethod
    def looks_like_export(path: Path) -> bool:
        if path.is_dir():
            return (path / "_chat.txt").is_file()
        return path.suffix.lower() in (".txt", ".zip") and bool(_EXPORT_NAME_RE.match(path.name))

    @classmethod
    def discover(cls, home: Path, system: str) -> list[DiscoveredSource]:
        out: list[DiscoveredSource] = []
        for path in find_paths(search_dirs(home), cls.looks_like_export):
            estimate = None
            if path.suffix.lower() == ".txt":
                lines = _file_lines(path)()
                estimate = sum(1 for line in lines if parse_header(line.rstrip("\n")))
            out.append(
                cls.found(
                    path, estimate=estimate, label=f"WhatsApp export {display_path(path, home)}"
                )
            )
        return out
