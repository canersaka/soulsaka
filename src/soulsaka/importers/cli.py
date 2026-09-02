"""``soulsaka import``: bring existing message history into the corpus.

``soulsaka import --auto`` (or ``soulsaka import auto``) discovers iMessage, WhatsApp,
Apple Mail, dropped-in exports and git repositories on this machine and imports every
readable one. Each importer also has its own command for explicit paths.

Messages go to the paired hub when this machine has a token (``soulsaka hub login``),
otherwise straight into the local database; ``--local`` and ``--hub URL`` override that.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from soulsaka.config import get_settings
from soulsaka.identity import IdentityResolver
from soulsaka.importers.base import IMPORTERS, DiscoveredSource, Importer, ImporterError, run_import
from soulsaka.importers.discover import discover_all, has_email_source
from soulsaka.importers.sinks import DbSink, HubSink, open_sink
from soulsaka.models import ImportReport

import_app = typer.Typer(
    help="Import your message history: iMessage, WhatsApp, email, Discord, git, documents.",
    no_args_is_help=True,
    invoke_without_command=True,
)
console = Console()

LocalOpt = typer.Option(False, "--local", help="Write to the local database, not the hub.")
HubOpt = typer.Option(None, "--hub", help="Push to this hub URL instead of the paired one.")
DryRunOpt = typer.Option(False, "--dry-run", help="Only show what would be imported.")
MeOpt = typer.Option([], "--me", help="Your name or email as it appears in the source. Repeatable.")

IMAP_HINT = (
    "No local email found. Pull your sent mail with: "
    "soulsaka import imap --host imap.gmail.com --user you@gmail.com"
)


# --- helpers -------------------------------------------------------------------------------


def _identity(me: list[str]) -> IdentityResolver:
    base = IdentityResolver.from_settings(get_settings())
    names = [m for m in me if "@" not in m]
    emails = [m for m in me if "@" in m]
    return IdentityResolver(
        names=[*base.names, *names], emails=[*base.emails, *emails], phones=list(base.phones)
    )


def _sources_table(sources: list[DiscoveredSource]) -> Table:
    t = Table(title="discovered sources")
    t.add_column("kind")
    t.add_column("source")
    t.add_column("estimate", justify="right")
    t.add_column("status")
    for s in sources:
        estimate = f"{s.estimate:,}" if s.estimate is not None else ""
        status = "[green]ready[/]" if s.available else f"[yellow]{escape(s.reason or 'n/a')}[/]"
        if s.available and s.reason:
            status += f" [dim]{escape(s.reason)}[/]"
        t.add_row(s.importer_kind or s.kind, escape(s.label), estimate, status)
    return t


def _reports_table(reports: list[ImportReport]) -> Table:
    t = Table(title="import")
    t.add_column("source")
    for col in ("received", "inserted", "duplicates", "skipped", "me words", "conversations"):
        t.add_column(col, justify="right")
    total = ImportReport(source=reports[0].source) if reports else None
    for r in reports:
        t.add_row(
            escape(r.source.label),
            f"{r.received:,}",
            f"{r.inserted:,}",
            f"{r.duplicates:,}",
            f"{r.skipped:,}",
            f"{r.me_words:,}",
            f"{r.conversations:,}",
        )
        if total is not None:
            total.merge(r)
    if total is not None and len(reports) > 1:
        conversations = sum(r.conversations for r in reports)
        t.add_row(
            "[bold]total[/]",
            f"[bold]{total.received:,}[/]",
            f"[bold]{total.inserted:,}[/]",
            f"[bold]{total.duplicates:,}[/]",
            f"[bold]{total.skipped:,}[/]",
            f"[bold]{total.me_words:,}[/]",
            f"[bold]{conversations:,}[/]",
        )
    return t


def _run(
    jobs: list[tuple[Importer, int | None]], *, local: bool, hub: str | None
) -> list[ImportReport]:
    """Import every (importer, estimate) into the chosen sink and print the reports."""
    if not jobs:
        rprint("[yellow]nothing to import[/]")
        return []
    sink: DbSink | HubSink = open_sink(local=local, hub=hub)
    rprint(f"[dim]destination: {escape(sink.describe())}[/]")
    reports: list[ImportReport] = []
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            for importer, estimate in jobs:
                task = progress.add_task(importer.label, total=estimate)
                try:
                    report = run_import(
                        importer,
                        sink,
                        progress=lambda n, task=task: progress.update(task, completed=n),
                    )
                except ImporterError as e:
                    progress.remove_task(task)
                    rprint(f"[red]{escape(importer.label)}:[/] {escape(str(e))}")
                    continue
                progress.update(task, total=report.received, completed=report.received)
                reports.append(report)
        if reports:
            console.print(_reports_table(reports))
            for r in reports:
                for note in r.notes:
                    rprint(f"  [dim]{escape(note)}[/]")
                for reason, n in sorted(r.skipped_reasons.items()):
                    rprint(f"  [dim]{escape(r.source.label)}: skipped {n:,} {reason}[/]")
            words = sink.me_words()
            color = "green" if words >= 30_000 else "yellow"
            rprint(f"[bold {color}]{words:,}[/] words of you in the corpus (soulsaka stats for more)")
    finally:
        sink.close()
    return reports


def _build(source: DiscoveredSource, identity: IdentityResolver) -> Importer:
    cls = IMPORTERS[source.importer_kind]
    return cls(source.locator, identity=identity)


# --- auto ----------------------------------------------------------------------------------


@import_app.callback()
def import_main(
    ctx: typer.Context,
    auto: bool = typer.Option(False, "--auto", help="Same as `soulsaka import auto`."),
    dry_run: bool = DryRunOpt,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
    me: list[str] = MeOpt,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if not auto:
        typer.echo(ctx.get_help())
        raise typer.Exit()
    auto_cmd(dry_run=dry_run, yes=yes, local=local, hub=hub, me=me)


@import_app.command("auto")
def auto_cmd(
    dry_run: bool = DryRunOpt,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
    me: list[str] = MeOpt,
) -> None:
    """Find iMessage, WhatsApp, Mail, exports and git repos on this machine and import them."""
    with console.status("looking for message history..."):
        sources = discover_all()
    console.print(_sources_table(sources))
    if not has_email_source(sources):
        rprint(f"[dim]{IMAP_HINT}[/]")
    available = [s for s in sources if s.available]
    if not available:
        rprint("[yellow]nothing readable found[/]")
        raise typer.Exit(1)
    if dry_run:
        return
    if (
        not yes
        and sys.stdin.isatty()
        and not typer.confirm(f"Import {len(available)} sources?", default=True)
    ):
        raise typer.Exit()
    identity = _identity(me)
    jobs = [(_build(s, identity), s.estimate) for s in available]
    _run(jobs, local=local, hub=hub)


# --- one command per importer ----------------------------------------------------------------


@import_app.command("imessage")
def imessage_cmd(
    path: Path | None = typer.Argument(None, help="chat.db (default ~/Library/Messages/chat.db)"),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """iMessage history from Messages.app's chat.db (needs Full Disk Access)."""
    cls = IMPORTERS["imessage"]
    target = path or cls.default_path()  # type: ignore[attr-defined]
    _run([(cls(target, identity=_identity([])), None)], local=local, hub=hub)


@import_app.command("whatsapp")
def whatsapp_cmd(
    path: Path | None = typer.Argument(None, help="ChatStorage.sqlite of WhatsApp Desktop."),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """WhatsApp Desktop (Mac) message database."""
    cls = IMPORTERS["whatsapp"]
    target = path or cls.default_path()  # type: ignore[attr-defined]
    _run([(cls(target, identity=_identity([])), None)], local=local, hub=hub)


@import_app.command("whatsapp-export")
def whatsapp_export_cmd(
    path: Path = typer.Argument(..., help="A .txt export, a .zip, or a folder of them."),
    me: list[str] = MeOpt,
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """WhatsApp chat exports (Settings → Chats → Export chat)."""
    importer = IMPORTERS["whatsapp_export"](path, identity=_identity(me))
    _run([(importer, None)], local=local, hub=hub)


@import_app.command("mbox")
def mbox_cmd(
    path: Path = typer.Argument(..., help="An .mbox file, e.g. from Google Takeout."),
    me: list[str] = MeOpt,
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Gmail Takeout / any mbox file."""
    importer = IMPORTERS["mbox"](path, identity=_identity(me))
    _run([(importer, None)], local=local, hub=hub)


@import_app.command("emlx")
def emlx_cmd(
    path: Path | None = typer.Argument(None, help="Mail folder (default ~/Library/Mail)."),
    me: list[str] = MeOpt,
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Apple Mail: Sent mailboxes plus INBOX for context (needs Full Disk Access)."""
    cls = IMPORTERS["emlx"]
    target = path or cls.default_path()  # type: ignore[attr-defined]
    _run([(cls(target, identity=_identity(me)), None)], local=local, hub=hub)


@import_app.command("imap")
def imap_cmd(
    host: str = typer.Option(..., help="IMAP server, e.g. imap.gmail.com"),
    user: str = typer.Option(..., help="Login, usually your email address."),
    password: str | None = typer.Option(
        None, help="Password; else SOULSAKA_IMAP_PASSWORD or a prompt."
    ),
    folder: list[str] = typer.Option(
        [], help="Folder(s) to import; default: the Sent folder. Repeatable."
    ),
    since: str | None = typer.Option(None, help="Only messages since YYYY-MM-DD."),
    port: int = typer.Option(993, help="IMAP over TLS port."),
    me: list[str] = MeOpt,
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Sent mail straight from an IMAP server."""
    from soulsaka.importers.imap import GMAIL_APP_PASSWORD_NOTE

    if "gmail" in host.lower():
        rprint(f"[dim]{GMAIL_APP_PASSWORD_NOTE}[/]")
    secret = password or os.environ.get("SOULSAKA_IMAP_PASSWORD") or ""
    if not secret:
        secret = typer.prompt("password", hide_input=True)
    since_day = date.fromisoformat(since) if since else None
    identity = _identity([*me, user] if "@" in user else me)
    importer = IMPORTERS["imap"](  # type: ignore[call-arg]
        host, user, secret, folders=folder, since=since_day, port=port, identity=identity
    )
    _run([(importer, None)], local=local, hub=hub)


@import_app.command("discord")
def discord_cmd(
    path: Path = typer.Argument(..., help="package.zip or the extracted folder."),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Discord data package (all messages are yours)."""
    _run([(IMPORTERS["discord"](path, identity=_identity([])), None)], local=local, hub=hub)


@import_app.command("git")
def git_cmd(
    roots: list[Path] = typer.Argument(None, help="Folders to search (default ~)."),
    me: list[str] = MeOpt,
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Commit messages you wrote, in repositories under the given folders."""
    importer = IMPORTERS["git"](identity=_identity(me), roots=roots or None)  # type: ignore[call-arg]
    _run([(importer, None)], local=local, hub=hub)


@import_app.command("docs")
def docs_cmd(
    path: Path = typer.Argument(..., help="Folder of .md / .txt files you wrote."),
    local: bool = LocalOpt,
    hub: str | None = HubOpt,
) -> None:
    """Your own writing: notes, essays, journals."""
    _run([(IMPORTERS["docs"](path, identity=_identity([])), None)], local=local, hub=hub)
