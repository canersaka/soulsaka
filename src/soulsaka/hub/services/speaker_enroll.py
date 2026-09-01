"""Building the "me" voice profile from clips that are known to be me."""

from __future__ import annotations

import logging
from pathlib import Path

from soulsaka.db import captures as captures_db
from soulsaka.hub.state import HubState

log = logging.getLogger(__name__)


def enroll_paths(state: HubState, paths: list[Path]) -> dict:
    speaker = state.service("speaker")
    centroid, n = speaker.enroll(state.db, [Path(p) for p in paths])
    state.events.publish("speaker_profile", n_samples=n)
    return speaker.status(state.db)


def enroll_from_capture(state: HubState, uid: str) -> None:
    cap = captures_db.get_capture_row(state.db, uid)
    if not cap or not cap.get("audio_path") or cap.get("kind") != "audio":
        return
    if cap.get("origin") != "manual":
        return  # only push-to-talk clips are guaranteed to be me
    path = state.abs_path(cap["audio_path"])
    if not path.exists():
        return
    enroll_paths(state, [path])
