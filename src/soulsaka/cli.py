"""Command line entry point: ``soulsaka``."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from soulsaka import __version__
from soulsaka.config import get_settings, reset_settings, settings_to_toml
from soulsaka.paths import config_path, data_dir, ensure_layout

app = typer.Typer(
    help="soulsaka: a private clone of how you write and speak, on your own hardware.",
    no_args_is_help=True,
)
hub_app = typer.Typer(help="Pair this machine with a hub running elsewhere.", no_args_is_help=True)
devices_app = typer.Typer(help="Paired devices.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration.", no_args_is_help=True)
jobs_app = typer.Typer(help="Background jobs.", no_args_is_help=True)
voice_app = typer.Typer(help="Voice profile and TTS.", no_args_is_help=True)
memory_app = typer.Typer(help="Memories.", no_args_is_help=True)
app.add_typer(hub_app, name="hub")
app.add_typer(devices_app, name="devices")
app.add_typer(config_app, name="config")
app.add_typer(jobs_app, name="jobs")
app.add_typer(voice_app, name="voice")
app.add_typer(memory_app, name="memory")

console = Console()


def _state():
    from soulsaka.hub.state import HubState

    return HubState(get_settings())


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")):
    _setup_logging(verbose)


@app.command()
def version():
    """Print the version."""
    rprint(f"soulsaka {__version__}")


@app.command()
def init(
    name: str = typer.Option("", help="Your display name as it appears in chat exports."),
    email: list[str] = typer.Option([], help="Your email address(es). Repeatable."),
    phone: list[str] = typer.Option([], help="Your phone number(s). Repeatable."),
    force: bool = typer.Option(False, help="Overwrite an existing config.toml."),
):
    """Create the data directory and a commented config.toml."""
    root = ensure_layout()
    path = config_path()
    if path.exists() and not force:
        rprint(f"[yellow]config already exists:[/] {path} (use --force to overwrite)")
    else:
        reset_settings()
        settings = get_settings()
        if name:
            settings.me.display_name = name
            settings.me.names = [name]
        if email:
            settings.me.emails = list(email)
        if phone:
            settings.me.phones = list(phone)
        path.write_text(settings_to_toml(settings), encoding="utf-8")
        rprint(f"[green]wrote[/] {path}")
    reset_settings()
    _state().close()
    rprint(f"data directory: {root}")
    rprint(
        "next: [bold]soulsaka serve[/] on the machine with the GPU, then [bold]soulsaka import --auto[/] on the machine with your messages."
    )


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default from config, 0.0.0.0)."),
    port: int | None = typer.Option(None, help="Port (default from config, 8765)."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
):
    """Run the hub: API, web app and background workers."""
    import uvicorn

    from soulsaka.db import devices as devices_db
    from soulsaka.hub.netinfo import hub_urls

    settings = get_settings()
    host = host or settings.hub.host
    port = port or settings.hub.port
    state = _state()
    code = devices_db.create_pairing_code(state.db, ttl_s=1800)
    state.close()
    urls = hub_urls(port)
    rprint("[bold]soulsaka hub[/]")
    for u in urls:
        rprint(f"  open {u}")
    rprint(f"  pairing code (30 min): [bold cyan]{code}[/]")
    rprint(
        f"  on another machine: soulsaka hub login --url {urls[-1] if len(urls) > 1 else urls[0]} --code {code}"
    )
    uvicorn.run(
        "soulsaka.hub.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def pair(ttl: int = typer.Option(600, help="Seconds the code stays valid.")):
    """Print a fresh pairing code for another device."""
    from soulsaka.db import devices as devices_db

    state = _state()
    code = devices_db.create_pairing_code(state.db, ttl_s=ttl)
    state.close()
    rprint(f"pairing code: [bold cyan]{code}[/] (valid {ttl // 60} min)")


@app.command()
def stats():
    """The number: how many words of you the corpus holds."""
    from soulsaka.client import ClientConfig, HubClient
    from soulsaka.db import corpus as corpus_db

    cfg = ClientConfig.load()
    if cfg and cfg.token:
        s = HubClient.from_config().stats()
    else:
        state = _state()
        s = corpus_db.stats(state.db)
        state.close()
    _print_stats(s)


def _print_stats(s) -> None:
    color = "green" if s.ready_for_first_train else "yellow"
    rprint(
        f"[bold {color}]{s.me_words:,}[/] words of you in {s.me_messages:,} messages "
        f"({s.other_messages:,} context messages from others, {s.conversations:,} conversations)"
    )
    if not s.ready_for_first_train:
        rprint(
            f"  need {s.first_train_threshold - s.me_words:,} more for a first train "
            f"({s.first_train_threshold:,}); {s.comfortable_threshold:,} is comfortable"
        )
    t = Table(title="by register")
    t.add_column("register")
    t.add_column("messages", justify="right")
    t.add_column("words", justify="right")
    for r in s.by_register:
        t.add_row(r.register, f"{r.messages:,}", f"{r.words:,}")
    console.print(t)
    if s.by_source:
        t = Table(title="by source")
        t.add_column("source")
        t.add_column("messages", justify="right")
        t.add_column("words", justify="right")
        for r in s.by_source:
            t.add_row(f"{r.kind}: {r.label}", f"{r.messages:,}", f"{r.words:,}")
        console.print(t)
    if s.by_lang:
        rprint(
            "languages: "
            + ", ".join(f"{k} {v:,}" for k, v in sorted(s.by_lang.items(), key=lambda kv: -kv[1]))
        )
    rprint(
        f"memories: {s.memories:,}   pending captures: {s.captures_pending:,}   latest adapter: {s.latest_version or 'none'}"
    )


@app.command()
def note(text: str, origin: str = typer.Option("manual", help="manual | listener | chat")):
    """Quick text capture. Goes to the paired hub, or straight into the local database."""
    from soulsaka.client import ClientConfig, HubClient

    cfg = ClientConfig.load()
    if cfg and cfg.token:
        out = HubClient.from_config().capture_text(text, origin=origin)
        rprint(f"sent to hub: {out.uid}")
        return
    from soulsaka.db import captures as captures_db
    from soulsaka.hub.jobs import JobRunner, default_handlers
    from soulsaka.models import CaptureIn
    from soulsaka.util.ids import new_uid
    from soulsaka.util.time import utcnow

    state = _state()
    cap = CaptureIn(uid=new_uid(), kind="text", origin=origin, client_ts=utcnow(), text=text)  # type: ignore[arg-type]
    captures_db.create_capture(state.db, "local", cap)
    from soulsaka.db import jobs as jobs_db

    jobs_db.enqueue(state.db, "process_capture", {"uid": cap.uid})
    runner = JobRunner(state)
    for k, h in default_handlers().items():
        runner.register(k, h)
    runner.drain()
    out = captures_db.get_capture(state.db, cap.uid)
    state.close()
    rprint(f"captured ({out.status}); memories: {', '.join(out.memory_uids) or 'none'}")


@app.command()
def chat(
    text: str,
    profile: str | None = typer.Option(None, help="LLM profile name (see config)."),
    mode: str = typer.Option("assistant", help="assistant | twin"),
):
    """One-shot chat against the local hub state."""
    from soulsaka.hub.services import chat as chat_svc

    state = _state()
    try:
        out = chat_svc.respond(
            state, text=text, device_uid="local", profile=profile, mode=mode, stream=False
        )
        rprint(f"[dim]{out.profile} / {out.model}[/]")
        print(out.text)
    finally:
        state.close()


# -- hub pairing (client side) -------------------------------------------------------


@hub_app.command("login")
def hub_login(
    url: str = typer.Option(..., help="Hub URL, e.g. http://192.168.1.20:8765"),
    code: str = typer.Option(..., help="Pairing code shown by `soulsaka serve` or `soulsaka pair`."),
    name: str = typer.Option("", help="Name for this device."),
    kind: str = typer.Option("cli", help="browser | listener | importer | cli"),
):
    """Pair this machine with a hub and remember the token."""
    import platform

    from soulsaka.client import ClientConfig, HubClient

    client = HubClient(url)
    resp = client.pair(code, name or platform.node() or "device", kind)
    ClientConfig(hub_url=url.rstrip("/"), token=resp.token, device_uid=resp.device_uid).save()
    rprint(f"[green]paired[/] as {resp.device_uid} with {url}")


@hub_app.command("status")
def hub_status():
    """Check the paired hub."""
    from soulsaka.client import ClientConfig, HubClient

    cfg = ClientConfig.load()
    if not cfg:
        rprint("[yellow]not paired[/]; run `soulsaka hub login`")
        raise typer.Exit(1)
    h = HubClient.from_config().health()
    rprint(f"hub {cfg.hub_url}: version {h['version']}, {h['devices']} devices")


@hub_app.command("logout")
def hub_logout():
    from soulsaka.client import ClientConfig

    p = ClientConfig.path()
    if p.exists():
        p.unlink()
    rprint("forgot hub credentials")


# -- devices ---------------------------------------------------------------------------


@devices_app.command("list")
def devices_list():
    from soulsaka.db import devices as devices_db

    state = _state()
    t = Table(title="devices")
    for col in ("uid", "name", "kind", "created", "last seen"):
        t.add_column(col)
    for d in devices_db.list_devices(state.db):
        t.add_row(d.uid, d.name, d.kind, d.created_at[:19], (d.last_seen_at or "")[:19])
    console.print(t)
    state.close()


@devices_app.command("revoke")
def devices_revoke(uid: str):
    from soulsaka.db import devices as devices_db

    state = _state()
    ok = devices_db.revoke_device(state.db, uid)
    state.close()
    rprint("[green]revoked[/]" if ok else "[red]no such device[/]")


# -- config ----------------------------------------------------------------------------


@config_app.command("path")
def config_path_cmd():
    rprint(str(config_path()))


@config_app.command("show")
def config_show():
    reset_settings()
    print(settings_to_toml(get_settings()))


@config_app.command("data-dir")
def config_data_dir():
    rprint(str(data_dir()))


# -- jobs ------------------------------------------------------------------------------


@jobs_app.command("status")
def jobs_status():
    from soulsaka.db import jobs as jobs_db

    state = _state()
    rprint(jobs_db.counts(state.db))
    for j in jobs_db.recent(state.db, 15):
        rprint(
            f"  #{j['id']} {j['kind']} {j['status']} attempts={j['attempts']} {j['error'] or ''}"
        )
    state.close()


@jobs_app.command("drain")
def jobs_drain():
    """Run every queued job on this process (useful when the hub is not running)."""
    from soulsaka.hub.jobs import JobRunner, default_handlers

    state = _state()
    runner = JobRunner(state)
    for k, h in default_handlers().items():
        runner.register(k, h)
    n = runner.drain()
    state.close()
    rprint(f"ran {n} jobs")


# -- voice -----------------------------------------------------------------------------


@voice_app.command("enroll")
def voice_enroll(files: list[Path]):
    """Add clips of your voice to the speaker profile."""
    from soulsaka.hub.services.speaker_enroll import enroll_paths

    state = _state()
    status = enroll_paths(state, files)
    state.close()
    rprint(status)


@voice_app.command("status")
def voice_status():
    state = _state()
    rprint(state.service("speaker").status(state.db))
    state.close()


@voice_app.command("reset")
def voice_reset():
    state = _state()
    state.service("speaker").reset_profile(state.db)
    state.close()
    rprint("speaker profile cleared")


@voice_app.command("say")
def voice_say(text: str, out: Path = typer.Option(Path("out.wav"), help="Output WAV path.")):
    """Synthesize text in your voice."""
    state = _state()
    try:
        path = state.service("tts").synthesize(text, out)
        rprint(f"wrote {path}")
    finally:
        state.close()


# -- memory ----------------------------------------------------------------------------


@memory_app.command("list")
def memory_list(q: str | None = typer.Argument(None), limit: int = 30):
    from soulsaka.hub.services import retrieval

    state = _state()
    from soulsaka.db import memories as memories_db

    items = (
        retrieval.search_memories(state, q, k=limit)
        if q
        else memories_db.list_memories(state.db, limit=limit)
    )
    for m in items:
        rprint(f"[dim]{m.updated_at[:16]}[/] {escape('[' + m.kind + ']')} {escape(m.text)}")
    state.close()


@memory_app.command("add")
def memory_add(text: str, kind: str = "note"):
    from soulsaka.db import memories as memories_db

    state = _state()
    out, _ = memories_db.create_memory(state.db, text, kind=kind, source_kind="manual")
    state.close()
    rprint(f"added {out.uid}")


@memory_app.command("export")
def memory_export(out: Path = typer.Option(Path("memories.json"))):
    from soulsaka.db import memories as memories_db

    state = _state()
    items = [
        m.model_dump()
        for m in memories_db.list_memories(state.db, limit=100000, include_archived=True)
    ]
    out.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    state.close()
    rprint(f"wrote {len(items)} memories to {out}")


if __name__ == "__main__":
    app()
