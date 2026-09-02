"""Turning raw email into the words a person actually typed.

Shared by the mbox, emlx and IMAP importers. An email body is mostly other people's
words: quoted replies, forwarded headers, signatures, list footers, HTML chrome. This
module keeps the part above the first quote/signature marker and drops the rest.

Rules, in order of where they cut:

- ``-- `` on a line by itself starts a signature (RFC 3676).
- ``On <date>, <name> wrote:`` (also wrapped over two lines), Turkish ``... şunu yazdı:``,
  German ``schrieb:`` and friends start a quoted reply.
- ``-----Original Message-----`` / ``Begin forwarded message:`` / ``From: ... Sent: ...``
  header blocks start a forward.
- ``Sent from my iPhone``-style device lines and mailing-list footers (``To unsubscribe``,
  ``You received this message because...``, a rule of underscores) end the message.
- Lines starting with ``>`` are quotes wherever they are.

Threads are identified by Gmail's ``X-GM-THRID`` header when present, else by the
subject with reply/forward prefixes (``Re:``, ``Fwd:``, ``Ynt:``, ``İlt:``...) removed.
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser

from soulsaka.models import ImportedMessage
from soulsaka.text.normalize import clean_text, word_count

MAX_BODY_WORDS = 3000

_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:re|fwd?|fw|ynt|ilt|İlt|aw|wg|sv|vs|tr)\s*(?:\[\d+\])?\s*:\s*)+",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(r"^-- ?$")
_QUOTE_START_RE = re.compile(r"^(?:On |Le |Am |El |Il |Den |\d{1,2}[./ ]|\w{2,3}\.? ?\d)")
_QUOTE_END_RE = re.compile(
    r"(?:wrote|yazdı|yazdi|schrieb|escribió|a écrit|skrev)\s*:\s*$", re.IGNORECASE
)
_CUT_RE = re.compile(
    r"^(?:-{2,}\s*(?:original message|özgün ileti|orijinal ileti|forwarded message"
    r"|iletilen ileti|yönlendirilen ileti|ursprüngliche nachricht)\s*-{2,}"
    r"|begin forwarded message:|sent from my |get outlook for |iphone'umdan gönderildi"
    r"|android'imden gönderildi|you received this message because you are subscribed"
    r"|to unsubscribe|unsubscribe:?\s*$|_{10,}\s*$)",
    re.IGNORECASE,
)
_HEADER_BLOCK_RE = re.compile(r"^(?:From|Kimden|Von|De):\s", re.IGNORECASE)
_HEADER_FOLLOW_RE = re.compile(
    r"^(?:Sent|Date|To|Subject|Cc|Gönderme|Gönderen|Kime|Konu|Tarih|Gesendet|An|Betreff):\s",
    re.IGNORECASE,
)
_QUOTED_LINE_RE = re.compile(r"^\s*>")
_INLINE_IMAGE_RE = re.compile(r"\[(?:image|cid|resim):[^\]]*\]", re.IGNORECASE)
_AUTOMATED_ADDR_RE = re.compile(
    r"no-?_?reply|do-?not-?reply|mailer-daemon|postmaster|bounce|newsletter|notifications?@"
    r"|alerts?@|updates?@|digest|auto-?confirm|calendar-notification|feedback@",
    re.IGNORECASE,
)
SENT_LABELS = {"sent", "gönderilenler", "gönderilmiş postalar", "gönderilmiş"}


# --- HTML -------------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    """Visible text of an HTML mail, with block tags as newlines and quotes dropped."""

    _BLOCK = {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul",
        "ol", "pre", "hr", "section", "article", "header", "footer",
    }  # fmt: skip
    _SKIP = {"script", "style", "head", "title", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML: fall back to a crude strip
        return re.sub(r"<[^>]+>", " ", html)
    return parser.text()


# --- headers ----------------------------------------------------------------------------


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - undecodable header: keep the raw text
        return value


def from_address(msg: Message) -> tuple[str, str]:
    """``(display name, lower-cased address)`` of the sender."""
    name, addr = email.utils.parseaddr(decode_header_value(msg.get("From")))
    return name.strip(), addr.strip().lower()


def subject_key(subject: str) -> str:
    """Subject with reply/forward prefixes removed, normalised for thread matching."""
    stripped = _SUBJECT_PREFIX_RE.sub("", subject or "")
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def thread_key(msg: Message) -> str:
    thrid = (msg.get("X-GM-THRID") or "").strip()
    if thrid:
        return f"gm:{thrid}"
    key = subject_key(decode_header_value(msg.get("Subject")))
    if key:
        return f"subj:{key}"
    anchor = (msg.get("In-Reply-To") or msg.get("Message-ID") or "").strip()
    return f"mid:{anchor}" if anchor else "subj:"


def gmail_labels(msg: Message) -> set[str]:
    raw = decode_header_value(msg.get("X-Gmail-Labels"))
    return {label.strip().casefold() for label in raw.split(",") if label.strip()}


def is_automated(msg: Message, addr: str) -> bool:
    """Newsletters, notifications, bounces: never worth keeping as context."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id") or msg.get("List-Post"):
        return True
    if (msg.get("Precedence") or "").strip().lower() in {"bulk", "list", "junk"}:
        return True
    auto = (msg.get("Auto-Submitted") or "no").strip().lower()
    if auto and auto != "no":
        return True
    return bool(_AUTOMATED_ADDR_RE.search(addr))


def parse_date(msg: Message, fallback: str | None = None) -> datetime | None:
    """``Date`` header, else the newest ``Received`` stamp, else ``fallback``; aware UTC."""
    candidates: list[str | None] = [msg.get("Date")]
    candidates += [r.rsplit(";", 1)[-1] for r in msg.get_all("Received", [])[:3]]
    candidates.append(fallback)
    for raw in candidates:
        if not raw:
            continue
        try:
            dt = email.utils.parsedate_to_datetime(raw.strip())
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        try:
            return dt.astimezone(UTC)
        except (OverflowError, ValueError):
            continue
    return None


# --- body -------------------------------------------------------------------------------


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return payload.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def message_body(msg: Message) -> str:
    """The text/plain body, else text/html flattened to text. Attachments ignored."""
    plain: str | None = None
    html: str | None = None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if disposition.startswith("attachment"):
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_part(part)
        elif ctype == "text/html" and html is None:
            html = _decode_part(part)
    if plain and plain.strip():
        return plain
    if html:
        return html_to_text(html)
    return plain or ""


def _cut_index(lines: list[str]) -> int:
    """Index of the first line that starts a quote, signature, forward or footer."""
    for i, line in enumerate(lines):
        s = line.strip()
        if _SIGNATURE_RE.match(line.rstrip("\r")) or _CUT_RE.match(s):
            return i
        if _QUOTE_START_RE.match(s) and any(
            _QUOTE_END_RE.search(lines[j].strip()) for j in range(i, min(i + 3, len(lines)))
        ):
            return i
        if _HEADER_BLOCK_RE.match(s) and any(
            _HEADER_FOLLOW_RE.match(lines[j].strip()) for j in range(i + 1, min(i + 5, len(lines)))
        ):
            return i
    return len(lines)


def clean_email_body(text: str) -> str:
    """Keep what the sender typed; see the module docstring for what is dropped."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    kept = [line for line in lines[: _cut_index(lines)] if not _QUOTED_LINE_RE.match(line)]
    body = _INLINE_IMAGE_RE.sub("", "\n".join(kept))
    return clean_text(body)


# --- one parsed mail --------------------------------------------------------------------


@dataclass
class Mail:
    message_id: str | None
    from_name: str
    from_addr: str
    subject: str
    thread: str
    ts: datetime | None
    text: str
    automated: bool
    labels: set[str] = field(default_factory=set)

    def skip_reason(self) -> str | None:
        if self.ts is None:
            return "no_date"
        if not self.text:
            return "empty"
        if word_count(self.text) > MAX_BODY_WORDS:
            return "too_long"
        return None


def parse_mail(msg: Message, *, fallback_date: str | None = None) -> Mail:
    name, addr = from_address(msg)
    message_id = (msg.get("Message-ID") or "").strip() or None
    return Mail(
        message_id=message_id,
        from_name=name,
        from_addr=addr,
        subject=decode_header_value(msg.get("Subject")).strip(),
        thread=thread_key(msg),
        ts=parse_date(msg, fallback_date),
        text=clean_email_body(message_body(msg)),
        automated=is_automated(msg, addr),
        labels=gmail_labels(msg),
    )


def to_imported(mail: Mail, *, is_me: bool) -> ImportedMessage:
    assert mail.ts is not None
    return ImportedMessage(
        conversation_external_id=mail.thread,
        text=mail.text,
        ts=mail.ts,
        is_me=is_me,
        register="email",
        external_id=mail.message_id,
        conversation_title=mail.subject or None,
        is_group=False,
        sender_handle=None if is_me else (mail.from_addr or None),
        sender_name=None if is_me else (mail.from_name or None),
    )
