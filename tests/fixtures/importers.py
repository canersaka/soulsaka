"""Synthetic message sources for the importer tests.

Nothing personal is committed: every chat database, export, mailbox and repository is
built here at test time, shaped like the real thing (same tables, same line formats).
"""

from __future__ import annotations

import base64
import imaplib
import json
import os
import sqlite3
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

APPLE_EPOCH = 978_307_200
T0 = datetime(2024, 1, 2, 12, 45, 12, tzinfo=UTC)

ME_EMAIL = "me@example.com"
ALI_PHONE = "+16175550199"


def apple_ns(dt: datetime) -> int:
    return int(round((dt.timestamp() - APPLE_EPOCH) * 1_000_000_000))


def apple_s(dt: datetime) -> float:
    return dt.timestamp() - APPLE_EPOCH


# --- iMessage ---------------------------------------------------------------------------

_TYPEDSTREAM_HEAD = (
    b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x19NSMutableAttributedString\x00"
    b"\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x0f"
    b"NSMutableString\x01\x84\x84\x08NSString\x01\x94\x84\x01+"
)
_TYPEDSTREAM_TAIL = (
    b"\x86\x84\x02iI\x01\x0b\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x84"
    b"\x96\x96\x1d__kIMMessagePartAttributeName\x86\x92\x84\x84\x84\x08NSNumber\x00\x84\x84"
    b"\x07NSValue\x00\x94\x84\x01*\x84\x99\x99\x00\x86\x86\x86"
)


def typedstream(text: str) -> bytes:
    """An ``attributedBody`` blob the way Messages.app writes it (length-prefixed UTF-8)."""
    raw = text.encode("utf-8")
    if len(raw) < 0x80:
        length = bytes([len(raw)])
    elif len(raw) < 0x10000:
        length = b"\x81" + len(raw).to_bytes(2, "little")
    else:
        length = b"\x82" + len(raw).to_bytes(4, "little")
    return _TYPEDSTREAM_HEAD + length + raw + _TYPEDSTREAM_TAIL


LONG_TEXT = "long message " * 20  # > 128 bytes: exercises the 0x81 uint16 length form


def make_chat_db(path: Path, *, nanoseconds: bool = True) -> Path:
    """The subset of chat.db the importer reads, with the quirks it has to handle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
                           display_name TEXT, style INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
                              attributedBody BLOB, handle_id INTEGER, is_from_me INTEGER,
                              date INTEGER, item_type INTEGER DEFAULT 0,
                              associated_message_type INTEGER DEFAULT 0,
                              balloon_bundle_id TEXT, cache_has_attachments INTEGER DEFAULT 0);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO handle VALUES (?, ?, ?)",
        [
            (1, ALI_PHONE, "iMessage"),
            (2, "bob@example.com", "iMessage"),
            (3, "+905320000000", "SMS"),
        ],
    )
    conn.executemany(
        "INSERT INTO chat VALUES (?, ?, ?, ?, ?)",
        [
            (1, f"iMessage;-;{ALI_PHONE}", ALI_PHONE, None, 45),
            (2, "iMessage;+;chat123", "chat123", "Trip crew", 43),
        ],
    )
    conn.executemany("INSERT INTO chat_handle_join VALUES (?, ?)", [(1, 1), (2, 1), (2, 2), (2, 3)])

    def when(minutes: int) -> int:
        dt = T0 + timedelta(minutes=minutes)
        return apple_ns(dt) if nanoseconds else int(apple_s(dt))

    rows = [
        # ROWID, guid, text, attributedBody, handle_id, is_from_me, date, item_type, assoc, balloon
        (1, "g1", "hey what's up", None, 1, 0, when(0), 0, 0, None),
        (2, "g2", None, typedstream("not much, grinding for the exam tbh"), 0, 1, when(1), 0, 0, None),
        (3, "g3", 'Loved "hey what\'s up"', None, 0, 1, when(2), 0, 2000, None),
        (4, "g4", None, None, 2, 0, when(3), 1, 0, None),
        (5, "g5", "bu akşam gelir misin", None, 0, 1, when(4), 0, 0, None),
        (6, "g6", None, typedstream(LONG_TEXT), 2, 0, when(5), 0, 0, None),
        (7, "g7", "", None, 1, 0, when(6), 0, 0, "com.apple.messages.MSMessageExtensionBalloonProvider:x"),
        (8, "g8", "https://example.com/x", None, 0, 1, when(7), 0, 0, "com.apple.messages.URLBalloonProvider"),
        (9, "g9", "orphan message", None, 3, 0, when(8), 0, 0, None),
        (10, "g10", None, None, 1, 0, when(9), 0, 0, None),
    ]  # fmt: skip
    conn.executemany(
        "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, is_from_me, date, "
        "item_type, associated_message_type, balloon_bundle_id, cache_has_attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        rows,
    )
    conn.executemany(
        "INSERT INTO chat_message_join VALUES (?, ?)",
        [(1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6), (1, 7), (1, 8), (1, 10)],
    )
    conn.commit()
    conn.close()
    return path


# --- WhatsApp desktop --------------------------------------------------------------------

ALI_JID = "905321234567@s.whatsapp.net"
VELI_JID = "905559998877@s.whatsapp.net"
GROUP_JID = "123456-1600000000@g.us"


def make_whatsapp_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
                                     ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT, ZCONTACTNAME TEXT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZTEXT TEXT, ZISFROMME INTEGER,
                                 ZMESSAGEDATE REAL, ZCHATSESSION INTEGER, ZFROMJID TEXT,
                                 ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER, ZSTANZAID TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO ZWACHATSESSION VALUES (?, ?, ?, ?)",
        [(1, ALI_JID, "Ali", 0), (2, GROUP_JID, "Trip", 1), (3, "status@broadcast", None, 3)],
    )
    conn.executemany(
        "INSERT INTO ZWAGROUPMEMBER VALUES (?, ?, ?)", [(1, ALI_JID, "Ali"), (2, VELI_JID, "Veli")]
    )

    def when(minutes: int) -> float:
        return apple_s(T0 + timedelta(minutes=minutes))

    rows = [
        (1, "selam naber", 0, when(0), 1, ALI_JID, 0, None, "3A1"),
        (2, "iyiyim sen nasılsın", 1, when(1), 1, None, 0, None, "3A2"),
        (3, None, 0, when(2), 1, ALI_JID, 1, None, "3A3"),
        (4, "geliyor musun akşam", 0, when(3), 2, GROUP_JID, 0, 2, "3A4"),
        (5, "gelirim ya, saat 8 gibi", 1, when(4), 2, None, 0, None, "3A5"),
        (6, "status text", 0, when(5), 3, "status@broadcast", 0, None, "3A6"),
        (7, "", 1, when(6), 1, None, 0, None, "3A7"),
    ]
    conn.executemany("INSERT INTO ZWAMESSAGE VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


# --- WhatsApp exports --------------------------------------------------------------------

IOS_EXPORT = (
    "[1/2/24, 3:45:12 PM] Trip: ‎Messages and calls are end-to-end encrypted. No one outside of this chat, not even WhatsApp, can read or listen to them.\n"
    "[1/2/24, 3:45:13 PM] Trip: ‎Ali created group “Trip”\n"
    "[1/2/24, 3:46:00 PM] Ali: hey are you coming tonight?\n"
    "[1/2/24, 3:46:30 PM] Caner: yeah I think so\n"
    "we could meet at 8\n"
    "[1/2/24, 3:47:00 PM] Ali: ‎image omitted\n"
    "[1/13/24, 9:05:00 AM] Veli: ok\n"
    "‎[1/13/24, 9:06:00 AM] Caner: ‎<attached: 00000005-PHOTO-2024-01-13-09-06-00.jpg>\n"
)

ANDROID_EXPORT = (
    "2.01.2024 15:45 - Mesajlar ve aramalar uçtan uca şifrelidir. Bu sohbetin dışındaki hiç kimse, WhatsApp bile bu mesajları okuyamaz ve dinleyemez.\n"
    "2.01.2024 15:46 - Ali Veli: bu akşam geliyor musun\n"
    "2.01.2024 15:47 - Caner Saka: gelirim ya\n"
    "saat 8 gibi orada olurum\n"
    "2.01.2024 15:48 - Ali Veli: <Medya dahil edilmedi>\n"
    "13.01.2024 09:10 - Ali Veli: tamam görüşürüz\n"
)

PHONE_EXPORT = (
    "1/2/24, 3:45 PM - +90 532 000 00 00: numaram bu\n1/2/24, 3:46 PM - Caner: tamam kaydettim\n"
)


def make_export_zip(path: Path, text: str = IOS_EXPORT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_chat.txt", text)
        zf.writestr("00000005-PHOTO-2024-01-13-09-06-00.jpg", b"\xff\xd8")
    return path


# --- email -------------------------------------------------------------------------------


def _mbox_entry(sender: str, from_date: str, headers: list[tuple[str, str]], body: str) -> str:
    head = "\n".join(f"{k}: {v}" for k, v in headers)
    escaped = "\n".join(
        (">" + line if line.startswith("From ") else line) for line in body.split("\n")
    )
    return f"From {sender} {from_date}\n{head}\n\n{escaped}\n\n"


MBOX_FROM_DATE = "Tue Jan 02 15:45:12 +0000 2024"
_QUOTE = "On Tue, Jan 2, 2024 at 3:45 PM Ali Veli <ali@example.com> wrote:\n> are you coming tonight?\n> Ali"


def make_mbox(path: Path) -> Path:
    """A Gmail Takeout style mbox: a real thread, a newsletter, an unrelated mail, HTML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        _mbox_entry(
            "ali@example.com",
            MBOX_FROM_DATE,
            [
                ("From", "Ali Veli <ali@example.com>"),
                ("To", f"Caner Saka <{ME_EMAIL}>"),
                ("Subject", "Dinner"),
                ("Date", "Tue, 2 Jan 2024 15:45:12 +0300"),
                ("Message-ID", "<a1@example.com>"),
                ("X-GM-THRID", "111"),
                ("X-Gmail-Labels", "Inbox,Important"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            "are you coming tonight?\nAli",
        ),
        _mbox_entry(
            ME_EMAIL,
            MBOX_FROM_DATE,
            [
                ("From", f"Caner Saka <{ME_EMAIL}>"),
                ("To", "Ali Veli <ali@example.com>"),
                ("Subject", "Re: Dinner"),
                ("Date", "Tue, 2 Jan 2024 16:00:00 +0300"),
                ("Message-ID", "<m1@example.com>"),
                ("X-GM-THRID", "111"),
                ("X-Gmail-Labels", "Sent,Important"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            f"yes, around 8.\n\nBest,\nCaner\n\n{_QUOTE}",
        ),
        _mbox_entry(
            "news@newsletter.example.com",
            MBOX_FROM_DATE,
            [
                ("From", "Weekly <news@newsletter.example.com>"),
                ("To", ME_EMAIL),
                ("Subject", "Weekly digest"),
                ("Date", "Wed, 3 Jan 2024 09:00:00 +0000"),
                ("Message-ID", "<n1@example.com>"),
                ("X-GM-THRID", "222"),
                ("List-Unsubscribe", "<https://newsletter.example.com/u>"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            "This week in things.\n\nTo unsubscribe click here",
        ),
        _mbox_entry(
            "veli@example.com",
            MBOX_FROM_DATE,
            [
                ("From", "Veli <veli@example.com>"),
                ("To", ME_EMAIL),
                ("Subject", "Unrelated"),
                ("Date", "Wed, 3 Jan 2024 10:00:00 +0000"),
                ("Message-ID", "<v1@example.com>"),
                ("X-GM-THRID", "333"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            "nobody answered this",
        ),
        _mbox_entry(
            ME_EMAIL,
            MBOX_FROM_DATE,
            [
                ("From", f"Caner Saka <{ME_EMAIL}>"),
                ("To", "veli@example.com"),
                ("Subject", "Notes"),
                ("Date", "Thu, 4 Jan 2024 10:00:00 +0000"),
                ("Message-ID", "<m2@example.com>"),
                ("X-GM-THRID", "444"),
                ("X-Gmail-Labels", "Sent"),
                ("MIME-Version", "1.0"),
                ("Content-Type", 'multipart/alternative; boundary="b1"'),
            ],
            '--b1\nContent-Type: text/plain; charset="utf-8"\n\nhere are the notes\n-- \nCaner Saka\n'
            '--b1\nContent-Type: text/html; charset="utf-8"\n\n<div>here are the <b>notes</b></div>\n--b1--',
        ),
        _mbox_entry(
            ME_EMAIL,
            MBOX_FROM_DATE,
            [
                ("From", f"<{ME_EMAIL}>"),
                ("To", "veli@example.com"),
                ("Subject", "Html only"),
                ("Date", "Fri, 5 Jan 2024 10:00:00 +0000"),
                ("Message-ID", "<m3@example.com>"),
                ("X-GM-THRID", "555"),
                ("X-Gmail-Labels", "Sent"),
                ("Content-Type", "text/html; charset=utf-8"),
            ],
            "<html><body><div>hello from html<br>second line</div>"
            "<blockquote>quoted stuff</blockquote></body></html>",
        ),
        _mbox_entry(
            ME_EMAIL,
            "Tue Jan 09 10:00:00 +0000 2024",
            [
                ("From", f"Caner Saka <{ME_EMAIL}>"),
                ("To", "veli@example.com"),
                ("Subject", "No date header"),
                ("Message-ID", "<m4@example.com>"),
                ("X-GM-THRID", "666"),
                ("X-Gmail-Labels", "Sent"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            "dateless but the From line knows",
        ),
    ]
    path.write_text("".join(entries), encoding="utf-8")
    return path


def make_emlx(path: Path, raw: bytes) -> Path:
    """Apple Mail's container: byte count line, the RFC 822 message, a plist of flags."""
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
        b"<key>flags</key><integer>8590195713</integer></dict></plist>\n"
    )
    path.write_bytes(f"{len(raw)}\n".encode() + raw + plist)
    return path


SENT_EMLX = (
    f"From: Caner Saka <{ME_EMAIL}>\r\nTo: Ali Veli <ali@example.com>\r\nSubject: Plan\r\n"
    "Date: Tue, 2 Jan 2024 15:45:12 +0300\r\nMessage-ID: <s1@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nlet's meet at 8\r\n\r\n-- \r\nCaner\r\n"
).encode()

_REPLY_BODY = (
    "tamam, bu akşam 8 iyi\n\nOn Tue, Jan 2, 2024 at 3:45 PM Caner Saka <me@example.com> wrote:\n"
    "> let's meet at 8\n"
)
INBOX_REPLY_EMLX = (
    "From: Ali Veli <ali@example.com>\r\n"
    f"To: Caner Saka <{ME_EMAIL}>\r\nSubject: Re: Plan\r\n"
    "Date: Tue, 2 Jan 2024 16:10:00 +0300\r\nMessage-ID: <r1@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
    + base64.b64encode(_REPLY_BODY.encode("utf-8")).decode("ascii")
    + "\r\n"
).encode()

INBOX_NEWSLETTER_EMLX = (
    "From: Weekly <news@newsletter.example.com>\r\n"
    f"To: {ME_EMAIL}\r\nSubject: Weekly digest\r\n"
    "Date: Wed, 3 Jan 2024 09:00:00 +0000\r\nMessage-ID: <n2@example.com>\r\n"
    "List-Unsubscribe: <https://newsletter.example.com/u>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nThis week in things.\r\n"
).encode()

INBOX_OTHER_EMLX = (
    "From: Veli <veli@example.com>\r\n"
    f"To: {ME_EMAIL}\r\nSubject: Offer\r\n"
    "Date: Wed, 3 Jan 2024 11:00:00 +0000\r\nMessage-ID: <o1@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nnobody answered this\r\n"
).encode()

DRAFT_EMLX = (
    f"From: Caner Saka <{ME_EMAIL}>\r\nSubject: Draft\r\n"
    "Date: Wed, 3 Jan 2024 12:00:00 +0000\r\nMessage-ID: <d1@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nunfinished thought\r\n"
).encode()


def make_mail_tree(mail_root: Path) -> Path:
    """``~/Library/Mail/V10/<account>/<Mailbox>.mbox/<uuid>/Data/0/Messages/<n>.emlx``."""
    account = mail_root / "V10" / "ACCOUNT-UUID"
    base = "UUID/Data/0/Messages"
    make_emlx(account / "Sent Messages.mbox" / base / "1.emlx", SENT_EMLX)
    make_emlx(account / "INBOX.mbox" / base / "2.emlx", INBOX_REPLY_EMLX)
    make_emlx(account / "INBOX.mbox" / base / "3.partial.emlx", INBOX_NEWSLETTER_EMLX)
    make_emlx(account / "INBOX.mbox" / base / "4.emlx", INBOX_OTHER_EMLX)
    make_emlx(account / "Drafts.mbox" / base / "5.emlx", DRAFT_EMLX)
    return mail_root


# --- Discord -----------------------------------------------------------------------------


def make_discord_package(root: Path) -> Path:
    messages = root / "messages"
    messages.mkdir(parents=True, exist_ok=True)
    (messages / "index.json").write_text(
        json.dumps(
            {"111": "Direct Message with alice", "222": "general in Some Server", "333": None}
        )
    )
    dm = messages / "c111"
    dm.mkdir()
    (dm / "channel.json").write_text(json.dumps({"id": "111", "type": 1, "recipients": ["1", "2"]}))
    (dm / "messages.json").write_text(
        json.dumps(
            [
                {"ID": "1", "Timestamp": "2024-01-02 15:45:12.123000+00:00", "Contents": "hey alice", "Attachments": ""},
                {"ID": "2", "Timestamp": "2024-01-02 15:46:00+00:00", "Contents": "", "Attachments": "x.png"},
                {"ID": "3", "Timestamp": "2024-01-02T15:47:00Z", "Contents": "see you there", "Attachments": ""},
            ]
        )
    )  # fmt: skip
    guild = messages / "c222"
    guild.mkdir()
    (guild / "channel.json").write_text(
        json.dumps(
            {"id": "222", "type": 0, "name": "general", "guild": {"id": "9", "name": "Some Server"}}
        )
    )
    (guild / "messages.csv").write_text(
        "ID,Timestamp,Contents,Attachments\n4,2019-05-01 12:34:56.789000+00:00,old style csv row,\n"
    )
    unknown = messages / "c333"
    unknown.mkdir()
    (unknown / "messages.json").write_text(
        json.dumps([{"ID": "5", "Timestamp": "2020-02-02 02:02:02+00:00", "Contents": "orphan channel", "Attachments": ""}])
    )  # fmt: skip
    return root


def make_discord_zip(path: Path, package_dir: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for file in sorted(package_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(package_dir).as_posix())
    return path


# --- git ---------------------------------------------------------------------------------

COMMIT_BODY = (
    "Add importer skeleton\n\nStreams messages in chunks so mailboxes never sit in memory.\n\n"
    "Signed-off-by: Caner <me@example.com>\n"
)


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Caner Saka",
        "GIT_AUTHOR_EMAIL": ME_EMAIL,
        "GIT_COMMITTER_NAME": "Caner Saka",
        "GIT_COMMITTER_EMAIL": ME_EMAIL,
        "GIT_AUTHOR_DATE": "2024-01-02T15:45:12+03:00",
        "GIT_COMMITTER_DATE": "2024-01-02T15:45:12+03:00",
    }

    def git(*args: str, **overrides: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=path,
            env={**env, **overrides},
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "Initial commit")
    git("commit", "-q", "--allow-empty", "-m", COMMIT_BODY)
    git("commit", "-q", "--allow-empty", "-m", "Fix typo in the setup guide")
    git(
        "commit", "-q", "--allow-empty", "-m", "Someone else's commit",
        GIT_AUTHOR_NAME="Other", GIT_AUTHOR_EMAIL="other@example.com",
    )  # fmt: skip
    git("commit", "-q", "--allow-empty", "-m", "Merge branch 'feature' into main")
    return path


# --- docs --------------------------------------------------------------------------------


def make_docs_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "essay.md").write_text(
        "---\ntitle: Essay\n---\n# Why I write\n\nBecause it helps me think.\n\n```python\nprint('code')\n```\n\nSecond paragraph of prose.\n",
        encoding="utf-8",
    )
    paragraphs = [" ".join(f"word{i}" for i in range(500)) for _ in range(4)]
    (root / "journal").mkdir()
    (root / "journal" / "2024.txt").write_text("\n\n".join(paragraphs), encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.md").write_text("hidden", encoding="utf-8")
    (root / "photo.png").write_bytes(b"\x89PNG")
    return root


# --- a whole Mac -------------------------------------------------------------------------


def make_mac_home(home: Path) -> Path:
    """A fake ``~`` with every source in the place ``soulsaka import --auto`` looks."""
    make_chat_db(home / "Library" / "Messages" / "chat.db")
    make_whatsapp_db(
        home
        / "Library"
        / "Group Containers"
        / "group.net.whatsapp.WhatsApp.shared"
        / "ChatStorage.sqlite"
    )
    make_mail_tree(home / "Library" / "Mail")
    downloads = home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "WhatsApp Chat with Ali Veli.txt").write_text(ANDROID_EXPORT, encoding="utf-8")
    make_export_zip(downloads / "WhatsApp Chat - Trip.zip")
    make_mbox(downloads / "Takeout" / "Mail" / "All mail Including Spam and Trash.mbox")
    make_discord_package(downloads / "package")
    make_git_repo(home / "code" / "proj")
    make_docs_dir(home / "Documents" / "notes")
    (home / "Desktop").mkdir(exist_ok=True)
    return home


# --- IMAP --------------------------------------------------------------------------------


class FakeIMAP:
    """Just enough of imaplib.IMAP4 for the importer: login, list, select, uid search/fetch."""

    def __init__(self, folders: dict[str, list[bytes]], *, fail_login: bool = False):
        self.folders = folders
        self.fail_login = fail_login
        self.selected: str | None = None
        self.searches: list[tuple] = []
        self.logged_out = False

    def login(self, user: str, password: str):
        if self.fail_login:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"Logged in"]

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        ]

    def select(self, mailbox: str, readonly: bool = False):
        name = mailbox.strip('"')
        if name not in self.folders:
            return "NO", [b"no such folder"]
        self.selected = name
        return "OK", [str(len(self.folders[name])).encode()]

    def uid(self, command: str, *args):
        assert self.selected is not None
        msgs = self.folders[self.selected]
        if command == "SEARCH":
            self.searches.append(args[1:])
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(msgs)))]
        if command == "FETCH":
            out: list = []
            for uid in args[0].split(","):
                raw = msgs[int(uid) - 1]
                out.append((f"{uid} (UID {uid} BODY[] {{{len(raw)}}})".encode(), raw))
                out.append(b")")
            return "OK", out
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]
