from __future__ import annotations

from soulsaka.identity import IdentityResolver, handle_hash, normalize_handle
from soulsaka.text.lang import guess_lang
from soulsaka.text.normalize import clean_text, low_signal_reason, word_count


def test_clean_text_strips_noise():
    assert clean_text("hey​ there ￼  friend\r\n\r\n\r\n\r\nyo") == "hey there friend\n\nyo"


def test_word_count():
    assert word_count("naber abi, what's up?") == 4
    assert word_count("") == 0


def test_low_signal():
    assert low_signal_reason("") == "empty"
    assert low_signal_reason("<Media omitted>") == "media_placeholder"
    assert low_signal_reason("https://example.com/x") == "url_only"
    assert low_signal_reason("😂😂") == "emoji_only"
    assert low_signal_reason("ok see you at 5") is None


def test_guess_lang():
    assert guess_lang("bu akşam çok yorgunum ama gelirim") == "tr"
    assert guess_lang("yeah that is what I was thinking too") == "en"
    assert guess_lang("12345") is None


def test_normalize_handle():
    assert normalize_handle("(617) 555-0199") == "+16175550199"
    assert normalize_handle("+90 532 000 00 00") == "+905320000000"
    assert normalize_handle("Foo@Example.com ") == "foo@example.com"
    assert normalize_handle("SomeUser") == "someuser"


def test_handle_hash_is_stable_and_salted():
    a = handle_hash("salt1", "617-555-0199")
    b = handle_hash("salt1", "+1 (617) 555 0199")
    c = handle_hash("salt2", "617-555-0199")
    assert a == b
    assert a != c


def test_identity_resolver():
    r = IdentityResolver(names=["Caner Saka"], emails=["Me@Example.com"], phones=["617-555-0199"])
    assert r.is_me(name="caner  saka")
    assert r.is_me(handle="me@example.com")
    assert r.is_me(handle="+16175550199")
    assert not r.is_me(name="Someone Else", handle="x@y.z")
