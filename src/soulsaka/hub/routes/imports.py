"""Import files dropped on the web app: WhatsApp exports, Discord packages, mailboxes,
or a copied chat database. The file is kept under ``uploads/`` named by its content
hash, so dropping the same export twice re-uses the same source and dedupes."""

from __future__ import annotations

import hashlib
import inspect
import re
import sqlite3
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from soulsaka.hub.auth import current_device, get_state
from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import IMPORTERS, ImporterError, run_import
from soulsaka.importers.sinks import DbSink
from soulsaka.models import ImportReport
from soulsaka.paths import data_dir

router = APIRouter()

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sqlite_tables(path: Path) -> set[str]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            return {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def detect_kind(path: Path) -> str | None:
    """Guess the importer from the file name and a peek at its contents."""
    name = path.name.lower()
    head = path.open("rb").read(64)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        if any(n.endswith("messages/index.json") or n == "index.json" for n in names):
            return "discord"
        if any(n.lower().endswith(".txt") for n in names):
            return "whatsapp_export"
        return None
    if head.startswith(b"SQLite format 3"):
        tables = _sqlite_tables(path)
        if "ZWAMESSAGE" in tables:
            return "whatsapp"
        if {"message", "handle"} <= tables:
            return "imessage"
        return None
    if name.endswith(".mbox") or head.startswith(b"From "):
        return "mbox"
    if name.endswith(".txt"):
        return "whatsapp_export"
    if name.endswith(".emlx"):
        return "emlx"
    return None


def _build_importer(kind: str, path: Path, identity: IdentityResolver, me: str | None):
    cls = IMPORTERS.get(kind)
    if cls is None:
        raise HTTPException(
            status_code=400, detail=f"unknown import kind {kind!r}; known: {sorted(IMPORTERS)}"
        )
    kwargs: dict = {"identity": identity}
    params = inspect.signature(cls.__init__).parameters
    if me and "me" in params:
        kwargs["me"] = me
    try:
        return cls(path, **kwargs)
    except TypeError as e:
        raise HTTPException(
            status_code=400, detail=f"{kind} cannot import an uploaded file: {e}"
        ) from e


@router.post("/import/upload", response_model=ImportReport)
def upload(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("auto"),
    me: str | None = Form(None),
    device=Depends(current_device),
):
    state = get_state(request)
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    safe = _SAFE.sub("_", Path(file.filename or "upload").name)[:80] or "upload"
    dest = data_dir() / "uploads" / f"{digest}_{safe}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(raw)
    resolved = kind if kind and kind != "auto" else detect_kind(dest)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail="could not tell what this file is; pass kind=whatsapp_export|discord|mbox|imessage|whatsapp",
        )
    identity = IdentityResolver.from_settings(state.settings)
    importer = _build_importer(resolved, dest, identity, me)
    sink = DbSink(state)
    try:
        report = run_import(importer, sink)
    except ImporterError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if report.inserted:
        state.events.publish("corpus", source=report.source.kind, inserted=report.inserted)
    return report


@router.get("/import/kinds")
def kinds(request: Request, device=Depends(current_device)):
    return sorted(IMPORTERS)
