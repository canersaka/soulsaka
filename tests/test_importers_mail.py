from __future__ import annotations

import email
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from soulsaka.db import corpus as corpus_db
from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import ImporterError, run_import
from soulsaka.importers.emlx import EmlxImporter, classify_mailbox, read_emlx
from soulsaka.importers.imap import (
    ImapImporter,
    imap_date,
    is_sent_folder,
    parse_list_line,
    quote_folder,
)
from soulsaka.importers.mailclean import (
    clean_email_body,
    html_to_text,
    is_automated,
    message_body,
    parse_date,
    subject_key,
    thread_key,
)
from soulsaka.importers.mbox import MboxImporter
from soulsaka.importers.sinks import DbSink
from tests.fixtures.importers import (
    INBOX_NEWSLETTER_EMLX,
    INBOX_OTHER_EMLX,
    INBOX_REPLY_EMLX,
    ME_EMAIL,
    SENT_EMLX,
    FakeIMAP,
    make_emlx,
    make_mail_tree,
    make_mbox,
)

ME = IdentityResolver(names=["Caner Saka"], emails=[ME_EMAIL])


# --- cleaning ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "Hi Ali,\n\nthanks, see you at 8.\n\nOn Mon, Jan 2, 2024 at 3:45 PM Ali <ali@example.com> wrote:\n> are you coming?\n",
            "Hi Ali,\n\nthanks, see you at 8.",
        ),
        (
            "Hi,\n\nyes.\n\nOn Mon, Jan 2, 2024 at 3:45 PM Ali Veli <\nali@example.com> wrote:\n> coming?\n",
            "Hi,\n\nyes.",
        ),
        (
            "Tamam gelirim\n\n2 Oca 2024 Pzt 15:45 tarihinde Ali Veli <ali@example.com>\nşunu yazdı:\n> geliyor musun\n",
            "Tamam gelirim",
        ),
        ("Sure thing\n-- \nCaner Saka\n+90 532\n", "Sure thing"),
        ("See below\n\n-----Original Message-----\nFrom: Ali\nSent: Monday\n\nblah", "See below"),
        (
            "Fwd\n\nFrom: Ali <ali@example.com>\nSent: Monday, January 2\nTo: me\nSubject: x\n\nblah",
            "Fwd",
        ),
        ("Quoted inline\n> old line\nmy answer\n> another\n", "Quoted inline\nmy answer"),
        ("Look [image: photo.png] here\n\nSent from my iPhone\n", "Look here"),
        ("Body\n\n____________________________________\nDev mailing list\n", "Body"),
        ("Body\n\nYou received this message because you are subscribed to x.\n", "Body"),
        ("Body\r\n\r\nTo unsubscribe from this list, click here\r\n", "Body"),
        ("Begin forwarded message:\n\nFrom: x", ""),
        ("From the top, not a header\nmore text", "From the top, not a header\nmore text"),
    ],
)
def test_clean_email_body(body, expected):
    assert clean_email_body(body) == expected


def test_subject_and_thread_keys():
    assert subject_key("Re: Ynt: İlt: Fwd: Hello  world") == "hello world"
    assert subject_key("RE: FW: AW: x") == "x"
    assert subject_key("") == ""
    msg = email.message_from_string("Subject: Re: Dinner\nX-GM-THRID: 111\n\nx")
    assert thread_key(msg) == "gm:111"
    msg = email.message_from_string("Subject: Fwd: Dinner plans\n\nx")
    assert thread_key(msg) == "subj:dinner plans"
    msg = email.message_from_string("Message-ID: <abc@x>\n\nx")
    assert thread_key(msg) == "mid:<abc@x>"
    msg = email.message_from_string("Subject: =?utf-8?q?Ynt=3A_Merhaba_d=C3=BCnya?=\n\nx")
    assert thread_key(msg) == "subj:merhaba dünya"


def test_is_automated():
    def check(headers: str, sender: str = "ali@example.com") -> bool:
        return is_automated(email.message_from_string(headers + "\n\nx"), sender)

    assert check("List-Unsubscribe: <x>")
    assert check("List-Id: dev.lists.example.com")
    assert check("Precedence: bulk")
    assert check("Auto-Submitted: auto-generated")
    assert check("", "no-reply@github.com") and check("", "notifications@github.com")
    assert check("", "newsletter@shop.example.com")
    assert not check("", "ali@example.com")
    assert not check("Auto-Submitted: no", "ali@example.com")


def test_html_and_body_selection():
    text = html_to_text(
        "<html><head><style>x{}</style></head><body><div>Hello<br>there</div>"
        "<blockquote>quoted<blockquote>nested</blockquote></blockquote><p>bye &amp; bye</p></body></html>"
    )
    assert text.split() == ["Hello", "there", "bye", "&", "bye"]
    html_only = email.message_from_string(
        "Content-Type: text/html\n\n<p>hi <b>there</b></p><blockquote>quoted</blockquote>"
    )
    assert message_body(html_only).strip() == "hi there"
    alt = email.message_from_string(
        'Content-Type: multipart/alternative; boundary="b"\n\n--b\nContent-Type: text/plain\n\n'
        "plain wins\n--b\nContent-Type: text/html\n\n<p>html</p>\n--b--\n"
    )
    assert message_body(alt).strip() == "plain wins"
    empty_plain = email.message_from_string(
        'Content-Type: multipart/alternative; boundary="b"\n\n--b\nContent-Type: text/plain\n\n'
        "\n--b\nContent-Type: text/html\n\n<p>html only</p>\n--b--\n"
    )
    assert message_body(empty_plain).strip() == "html only"
    with_attachment = email.message_from_string(
        'Content-Type: multipart/mixed; boundary="b"\n\n--b\nContent-Type: text/plain\n\n'
        "body\n--b\nContent-Type: text/plain\nContent-Disposition: attachment; filename=a.txt\n\n"
        "attached\n--b--\n"
    )
    assert message_body(with_attachment).strip() == "body"


def test_parse_date_fallbacks():
    msg = email.message_from_string("Date: Tue, 2 Jan 2024 15:45:12 +0300\n\nx")
    assert parse_date(msg) == datetime(2024, 1, 2, 12, 45, 12, tzinfo=UTC)
    msg = email.message_from_string("Received: from a by b; Wed, 3 Jan 2024 09:00:00 +0000\n\nx")
    assert parse_date(msg) == datetime(2024, 1, 3, 9, 0, tzinfo=UTC)
    msg = email.message_from_string("Date: garbage\n\nx")
    assert parse_date(msg) is None
    assert parse_date(msg, "Tue Jan 09 10:00:00 +0000 2024") == datetime(
        2024, 1, 9, 10, 0, tzinfo=UTC
    )
    naive = email.message_from_string("Date: Tue, 2 Jan 2024 15:45:12 -0000\n\nx")
    assert parse_date(naive) == datetime(2024, 1, 2, 15, 45, 12, tzinfo=UTC)


# --- mbox ----------------------------------------------------------------------------------


def test_mbox_import(tmp_path):
    path = make_mbox(tmp_path / "mail.mbox")
    msgs = list(MboxImporter(path, identity=ME).iter_messages())
    by_id = {m.external_id: m for m in msgs}
    # newsletter (list header) and the thread nobody from me answered are skipped
    assert list(by_id) == [
        "<a1@example.com>",
        "<m1@example.com>",
        "<m2@example.com>",
        "<m3@example.com>",
        "<m4@example.com>",
    ]
    assert all(m.register == "email" and not m.is_group for m in msgs)
    ali = by_id["<a1@example.com>"]
    assert not ali.is_me and ali.sender_handle == "ali@example.com"
    assert ali.sender_name == "Ali Veli"
    assert ali.conversation_external_id == "gm:111" and ali.conversation_title == "Dinner"
    assert ali.ts == datetime(2024, 1, 2, 12, 45, 12, tzinfo=UTC)
    assert ali.text == "are you coming tonight?\nAli"
    reply = by_id["<m1@example.com>"]
    assert reply.is_me and reply.text == "yes, around 8.\n\nBest,\nCaner"
    assert reply.sender_handle is None and reply.sender_name is None
    assert reply.conversation_external_id == "gm:111"
    assert by_id["<m2@example.com>"].text == "here are the notes"  # text/plain wins, signature cut
    assert by_id["<m3@example.com>"].text == "hello from html\nsecond line"
    assert by_id["<m4@example.com>"].ts == datetime(2024, 1, 9, 10, 0, tzinfo=UTC)  # From_ line


def test_mbox_me_from_gmail_labels_alone(tmp_path):
    path = make_mbox(tmp_path / "mail.mbox")
    msgs = list(MboxImporter(path, identity=IdentityResolver()).iter_messages())
    assert sum(m.is_me for m in msgs) == 4 and len(msgs) == 5


def test_mbox_without_me_raises(tmp_path):
    path = tmp_path / "other.mbox"
    path.write_text(
        "From ali@example.com Tue Jan 02 15:45:12 +0000 2024\nFrom: ali@example.com\n"
        "Subject: x\nDate: Tue, 2 Jan 2024 15:45:12 +0000\n\nhello\n\n",
        encoding="utf-8",
    )
    with pytest.raises(ImporterError, match="ali@example.com"):
        list(MboxImporter(path, identity=ME).iter_messages())
    with pytest.raises(ImporterError, match="not found"):
        list(MboxImporter(tmp_path / "missing.mbox", identity=ME).iter_messages())


def test_mbox_skips_long_bodies(tmp_path):
    path = tmp_path / "long.mbox"
    path.write_text(
        f"From {ME_EMAIL} Tue Jan 02 15:45:12 +0000 2024\nFrom: {ME_EMAIL}\nSubject: long\n"
        f"Date: Tue, 2 Jan 2024 15:45:12 +0000\n\n{'word ' * 3100}\n\n",
        encoding="utf-8",
    )
    importer = MboxImporter(path, identity=ME)
    assert list(importer.iter_messages()) == []
    assert importer.notes == ["mbox long.mbox: skipped 1 messages (too_long)"]
    assert importer.source_ref().label == "mbox long.mbox"


def test_mbox_discover_and_estimate(tmp_path):
    home = tmp_path / "home"
    path = make_mbox(home / "Downloads" / "Takeout" / "Mail" / "All mail.mbox")
    (home / "Downloads" / "Apple.mbox").mkdir()  # Apple Mail mailboxes are directories
    (found,) = MboxImporter.discover(home, "Darwin")
    assert found.locator == str(path) and found.estimate == 7 and found.kind == "email"
    assert found.importer_kind == "mbox" and found.available


def test_mbox_into_db(state, tmp_path):
    path = make_mbox(tmp_path / "mail.mbox")
    report = run_import(MboxImporter(path, identity=ME), DbSink(state))
    assert report.source.kind == "email" and report.inserted == 5 and report.me_words > 0
    s = corpus_db.stats(state.db)
    assert [r.register for r in s.by_register] == ["email"]
    assert s.other_messages == 1 and s.me_messages == 4


# --- emlx ----------------------------------------------------------------------------------


def test_read_emlx_and_mailbox_classification(tmp_path):
    inner = Path("UUID/Data/0/Messages/1.emlx")
    path = make_emlx(tmp_path / "Sent Messages.mbox" / inner, SENT_EMLX)
    assert read_emlx(path) == SENT_EMLX
    assert classify_mailbox(path) == "sent"
    assert classify_mailbox(tmp_path / "INBOX.mbox" / inner) == "context"
    assert classify_mailbox(tmp_path / "Archive.mbox" / inner) == "context"
    assert classify_mailbox(tmp_path / "[Gmail].mbox" / "Sent Mail.mbox" / inner) == "sent"
    assert classify_mailbox(tmp_path / "Sent Items.mbox" / inner) == "sent"
    assert classify_mailbox(tmp_path / "Drafts.mbox" / inner) is None
    assert classify_mailbox(tmp_path / "Deleted Messages.mbox" / inner) is None
    (tmp_path / "bad.emlx").write_bytes(b"not a count\nx")
    with pytest.raises(ValueError):
        read_emlx(tmp_path / "bad.emlx")


def test_emlx_import(tmp_path):
    root = make_mail_tree(tmp_path / "Mail")
    msgs = list(EmlxImporter(root, identity=ME).iter_messages())
    assert [(m.external_id, m.is_me) for m in msgs] == [
        ("<s1@example.com>", True),
        ("<r1@example.com>", False),
    ]
    sent, reply = msgs
    assert sent.text == "let's meet at 8"
    assert sent.ts == datetime(2024, 1, 2, 12, 45, 12, tzinfo=UTC)
    assert sent.conversation_external_id == reply.conversation_external_id == "subj:plan"
    assert reply.text == "tamam, bu akşam 8 iyi" and reply.sender_handle == "ali@example.com"
    assert reply.register == "email" and reply.conversation_title == "Re: Plan"
    one_mailbox = root / "V10" / "ACCOUNT-UUID" / "Sent Messages.mbox"
    assert len(list(EmlxImporter(one_mailbox, identity=ME).iter_messages())) == 1
    with pytest.raises(ImporterError, match="not found"):
        list(EmlxImporter(tmp_path / "nope", identity=ME).iter_messages())


def test_emlx_discover(tmp_path, monkeypatch):
    home = tmp_path / "home"
    make_mail_tree(home / "Library" / "Mail")
    (found,) = EmlxImporter.discover(home, "Darwin")
    assert found.available and found.estimate == 4 and "1 sent" in found.label
    assert found.kind == "email" and found.importer_kind == "emlx"
    (linux,) = EmlxImporter.discover(home, "Linux")
    assert not linux.available
    (missing,) = EmlxImporter.discover(tmp_path / "x", "Darwin")
    assert not missing.available and "not found" in missing.reason

    def denied(self, pattern):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "rglob", denied)
    (fda,) = EmlxImporter.discover(home, "Darwin")
    assert not fda.available and "Full Disk Access" in fda.reason
    with pytest.raises(ImporterError, match="Full Disk Access"):
        list(EmlxImporter(home / "Library" / "Mail", identity=ME).iter_messages())


def test_emlx_into_db(state, tmp_path):
    root = make_mail_tree(tmp_path / "Mail")
    report = run_import(EmlxImporter(root, identity=ME), DbSink(state))
    assert report.source.kind == "email" and report.source.label == "Apple Mail"
    assert report.inserted == 2 and report.me_words == 4 and report.conversations == 1


# --- imap ----------------------------------------------------------------------------------


def test_imap_helpers():
    assert imap_date(date(2020, 1, 2)) == "02-Jan-2020"
    assert quote_folder("[Gmail]/Sent Mail") == '"[Gmail]/Sent Mail"'
    assert parse_list_line(b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"') == (
        {"\\hasnochildren", "\\sent"},
        "[Gmail]/Sent Mail",
    )
    assert parse_list_line(b'(\\HasNoChildren) "." INBOX') == ({"\\hasnochildren"}, "INBOX")
    assert parse_list_line(b"garbage") is None
    assert is_sent_folder("Sent Items") and is_sent_folder("INBOX.Sent")
    assert not is_sent_folder("INBOX") and is_sent_folder("Foo", {"\\sent"})


def _fake() -> FakeIMAP:
    return FakeIMAP(
        {
            "[Gmail]/Sent Mail": [SENT_EMLX],
            "INBOX": [INBOX_REPLY_EMLX, INBOX_NEWSLETTER_EMLX, INBOX_OTHER_EMLX],
        }
    )


def test_imap_import_with_fake_server():
    fake = _fake()
    importer = ImapImporter("imap.gmail.com", ME_EMAIL, "app-pw", identity=ME, connect=lambda: fake)
    msgs = list(importer.iter_messages())
    assert [m.external_id for m in msgs] == ["<s1@example.com>"] and msgs[0].is_me
    assert msgs[0].register == "email"
    assert fake.selected == "[Gmail]/Sent Mail" and fake.logged_out
    assert fake.searches == [("ALL",)]
    ref = importer.source_ref()
    assert ref.kind == "email" and ref.locator == f"imap://{ME_EMAIL}@imap.gmail.com"

    fake = _fake()
    importer = ImapImporter(
        "imap.gmail.com",
        ME_EMAIL,
        "app-pw",
        folders=["[Gmail]/Sent Mail", "INBOX"],
        since=date(2020, 1, 2),
        identity=ME,
        connect=lambda: fake,
    )
    msgs = list(importer.iter_messages())
    assert [(m.external_id, m.is_me) for m in msgs] == [
        ("<s1@example.com>", True),
        ("<r1@example.com>", False),
    ]
    assert fake.searches[0] == ("SINCE", "02-Jan-2020")


def test_imap_errors():
    fake = FakeIMAP({}, fail_login=True)
    importer = ImapImporter("imap.gmail.com", ME_EMAIL, "wrong", connect=lambda: fake)
    with pytest.raises(ImporterError, match="app password"):
        list(importer.iter_messages())

    class NoSent(FakeIMAP):
        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    no_sent = NoSent({"INBOX": []})
    with pytest.raises(ImporterError, match="--folder"):
        list(ImapImporter("imap.example.com", "u", "p", connect=lambda: no_sent).iter_messages())
    missing = _fake()
    with pytest.raises(ImporterError, match="cannot open folder"):
        list(
            ImapImporter(
                "imap.example.com", "u", "p", folders=["Nope"], connect=lambda: missing
            ).iter_messages()
        )
