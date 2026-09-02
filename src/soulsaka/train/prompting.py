"""The one place that decides what the model is told about who it is.

Used by the dataset builder (training) and by chat in twin mode (inference) so the
two never drift apart.
"""

from __future__ import annotations

REGISTER_HINTS = {
    "text": "Text messages: short, casual, punctuation and capitalisation as you actually type them.",
    "email": "Email: complete sentences, a greeting and sign-off where you would normally use them.",
    "speech": "Spoken words, transcribed: reply the way it would be said out loud.",
    "doc": "Written prose or notes: paragraphs, considered wording.",
}

LANG_NAMES = {"en": "English", "tr": "Turkish"}


def system_prompt(
    name: str,
    *,
    register: str = "text",
    lang: str | None = None,
    setting: str | None = None,
) -> str:
    """System prompt for "reply as me" in a given register."""
    lines = [
        f"You are {name}. Write exactly as {name} would: same voice, wording, length and habits.",
        f"Register: {register}. {REGISTER_HINTS.get(register, '')}".strip(),
    ]
    if lang and lang in LANG_NAMES:
        lines.append(f"Language: {LANG_NAMES[lang]} (switch languages only if {name} would).")
    if setting:
        lines.append(f"Setting: {setting}")
    return "\n".join(lines)


def standalone_instruction(register: str, title: str | None) -> str:
    """The user turn for material that has no conversation partner (docs, commits, notes)."""
    what = {
        "doc": "Write the next piece",
        "speech": "Say what you were going to say",
        "text": "Write the note",
        "email": "Write the email",
    }.get(register, "Write")
    return f"{what} for: {title}" if title else f"{what}."
