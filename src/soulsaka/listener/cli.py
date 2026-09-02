"""``soulsaka listen``: the always-on microphone client.

    soulsaka listen [--device NAME|IDX] [--vad auto|silero|energy] [--threshold F]
                  [--no-upload] [--spool-max-mb N] [--quiet]
    soulsaka listen devices
    soulsaka listen file PATH [same options]

Requires a paired hub (``soulsaka hub login``) unless ``--no-upload`` is given, in which
case segments only accumulate in the local spool and nothing leaves the machine.
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from soulsaka.config import get_settings
from soulsaka.paths import logs_dir

listen_app = typer.Typer(
    help="Always-on microphone: keep speech, spool it locally, upload it to the hub.",
    invoke_without_command=True,
)
console = Console()
log = logging.getLogger(__name__)

# How long shutdown waits for the last segments to reach the hub.
FLUSH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RunOptions:
    vad: str = "auto"
    threshold: float | None = None
    upload: bool = True
    spool_max_mb: int = 2048
    quiet: bool = False


def _opt_vad() -> Any:
    return typer.Option(
        "auto", "--vad", help="Voice activity detector: auto | silero | energy.", show_default=True
    )


def _opt_threshold() -> Any:
    return typer.Option(
        None,
        "--threshold",
        min=0.0,
        max=1.0,
        help="Speech probability threshold (default: listener.vad_threshold from config).",
    )


def _opt_no_upload() -> Any:
    return typer.Option(
        False, "--no-upload", help="Only spool segments locally; never contact the hub."
    )


def _opt_spool_max_mb() -> Any:
    return typer.Option(
        2048, "--spool-max-mb", min=1, help="Delete the oldest spooled segments past this size."
    )


def _opt_quiet() -> Any:
    return typer.Option(
        False, "--quiet", "-q", help="No live status display; warnings and a summary only."
    )


@listen_app.callback()
def listen(
    ctx: typer.Context,
    device: str | None = typer.Option(
        None,
        "--device",
        help="Input device name (substring) or index; default: listener.device from config, "
        "else the system default. See `soulsaka listen devices`.",
    ),
    vad: str = _opt_vad(),
    threshold: float | None = _opt_threshold(),
    no_upload: bool = _opt_no_upload(),
    spool_max_mb: int = _opt_spool_max_mb(),
    quiet: bool = _opt_quiet(),
):
    """Listen on the microphone (the default when no subcommand is given)."""
    if ctx.invoked_subcommand is not None:
        return
    from soulsaka.listener.audio_input import MicSource

    if device is None:
        device = get_settings().listener.device
    opts = RunOptions(
        vad=vad, threshold=threshold, upload=not no_upload, spool_max_mb=spool_max_mb, quiet=quiet
    )
    raise typer.Exit(run_listener(MicSource(device), opts))


@listen_app.command("devices")
def devices():
    """List microphone (input) devices with the index and name `--device` accepts."""
    try:
        from soulsaka.listener.audio_input import list_input_devices

        found = list_input_devices()
    except (ImportError, OSError) as e:
        console.print(
            f"[red]sounddevice/PortAudio is not available:[/] {escape(str(e))}\n"
            "install the listener extra: [bold]uv sync --extra listener[/]"
        )
        raise typer.Exit(2) from None
    table = Table(title="input devices")
    table.add_column("index", justify="right")
    table.add_column("name")
    table.add_column("channels", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("")
    for d in found:
        table.add_row(
            str(d.index),
            d.name,
            str(d.channels),
            f"{d.default_samplerate:.0f}",
            "default" if d.is_default else "",
        )
    console.print(table)
    if not found:
        console.print("[yellow]no input devices found[/]")


@listen_app.command("file")
def file_(
    path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="WAV (or any soundfile format)."
    ),
    vad: str = _opt_vad(),
    threshold: float | None = _opt_threshold(),
    no_upload: bool = _opt_no_upload(),
    spool_max_mb: int = _opt_spool_max_mb(),
    quiet: bool = _opt_quiet(),
):
    """Segment an audio file instead of the microphone (for testing the pipeline)."""
    from soulsaka.listener.audio_input import FileSource

    opts = RunOptions(
        vad=vad, threshold=threshold, upload=not no_upload, spool_max_mb=spool_max_mb, quiet=quiet
    )
    raise typer.Exit(run_listener(FileSource(path), opts))


# -- the run ---------------------------------------------------------------------------


def run_listener(source: Any, opts: RunOptions) -> int:
    """Run the pipeline on ``source`` until it ends or Ctrl-C. Returns an exit code."""
    handler = _attach_file_log()
    try:
        return _run(source, opts)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def _attach_file_log() -> logging.Handler:
    """Log to ``logs_dir()/listener.log``; keep the terminal for the status display."""
    logs_dir().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs_dir() / "listener.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(max(h.level, logging.WARNING))
    root.addHandler(handler)
    return handler


def _run(source: Any, opts: RunOptions) -> int:
    from soulsaka.listener.listener import Listener
    from soulsaka.listener.segmenter import Segmenter
    from soulsaka.listener.spool import Spool
    from soulsaka.listener.uploader import TranscriptPoller, Uploader
    from soulsaka.listener.vad import make_vad

    cfg = get_settings().listener
    if cfg.sample_rate != 16000:
        log.warning("listener.sample_rate=%d ignored; the pipeline runs at 16 kHz", cfg.sample_rate)
    try:
        vad = make_vad(opts.vad)  # type: ignore[arg-type]
    except (RuntimeError, ValueError) as e:
        console.print(f"[red]{escape(str(e))}[/]")
        return 2
    segmenter = Segmenter(
        threshold=opts.threshold if opts.threshold is not None else cfg.vad_threshold,
        min_speech_s=cfg.min_speech_s,
        silence_end_s=cfg.silence_end_s,
        max_segment_s=cfg.max_segment_s,
        pad_s=cfg.pad_s,
    )
    spool = Spool(max_bytes=opts.spool_max_mb << 20)

    uploader: Uploader | None = None
    poller: TranscriptPoller | None = None
    hub_note = "upload disabled (--no-upload); segments stay in the spool"
    if opts.upload:
        from soulsaka.client import ClientConfig, HubClient, HubError

        try:
            client = HubClient.from_config()
        except HubError as e:
            console.print(f"[red]{escape(str(e))}[/] (or pass --no-upload)")
            return 1
        paired = ClientConfig.load()
        if paired is not None:
            poller = TranscriptPoller(paired.hub_url, paired.token)
        uploader = Uploader(spool, client, on_uploaded=poller.note if poller else None)
        try:
            client.health()
            hub_note = f"{client.base_url} reachable"
        except Exception as e:  # noqa: BLE001 - spooling is the whole point
            hub_note = f"{client.base_url} unreachable ({type(e).__name__}); spooling"
            log.warning("hub %s unreachable at start: %s", client.base_url, e)

    listener = Listener(source, vad, segmenter, spool, uploader)
    stop = threading.Event()
    worker = threading.Thread(target=listener.run, args=(stop,), name="soulsaka-capture", daemon=True)
    log.info(
        "listener starting: source=%s vad=%s threshold=%.2f spool=%s cap=%dMB upload=%s",
        source.name,
        vad.name,
        segmenter.threshold,
        spool.root,
        opts.spool_max_mb,
        opts.upload,
    )
    if uploader is not None:
        uploader.start()
    if poller is not None:
        poller.start()
    worker.start()

    def render() -> Panel:
        return _render(listener, uploader, poller, spool, hub_note)

    interrupted = False
    try:
        if opts.quiet:
            while worker.is_alive():
                worker.join(0.25)
        else:
            with Live(render(), console=console, refresh_per_second=8) as live:
                while worker.is_alive():
                    worker.join(0.125)
                    live.update(render())
                live.update(render())
    except KeyboardInterrupt:
        interrupted = True
        console.print("stopping; flushing the current segment")
    finally:
        stop.set()
        worker.join(FLUSH_TIMEOUT_S)
        if uploader is not None:
            uploader.wake()
            try:
                flushed = not spool.pending() or uploader.wait_idle(FLUSH_TIMEOUT_S)
            except KeyboardInterrupt:  # second Ctrl-C: do not wait for the hub
                flushed = False
            if not flushed:
                console.print(
                    f"[yellow]{spool.pending()} segment(s) still spooled in {spool.root}; "
                    "they upload next time[/]"
                )
            uploader.stop()
        if poller is not None:
            poller.stop()

    if not opts.quiet and not console.is_terminal:
        console.line()  # off a terminal, Live leaves the cursor on the panel's last line
    st = listener.status()
    if st.error:
        console.print(f"[red]listener error:[/] {escape(st.error)}")
        return 2
    up = uploader.stats() if uploader is not None else None
    parts = [f"{st.segments} segment(s) captured", f"{st.dropped} too short"]
    if up is not None:
        parts.append(f"{up.uploaded} uploaded")
    parts.append(f"{spool.pending()} spooled in {spool.root}")
    console.print("; ".join(parts))
    return 130 if interrupted else 0


# -- display -----------------------------------------------------------------------------


def _meter(level_db: float, width: int = 24) -> str:
    frac = min(1.0, max(0.0, (level_db + 60.0) / 60.0))
    n = int(round(frac * width))
    color = "red" if level_db > -6 else "yellow" if level_db > -20 else "green"
    return f"[{color}]{'█' * n}[/][dim]{'░' * (width - n)}[/]"


def _render(listener: Any, uploader: Any, poller: Any, spool: Any, hub_note: str) -> Panel:
    st = listener.status()
    up = uploader.stats() if uploader is not None else None
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", style="dim")
    grid.add_column()
    grid.add_row("input", f"{escape(listener.source.name)}  ·  vad {listener.vad.name}")
    grid.add_row("level", f"{_meter(st.level_db)} {st.level_db:6.1f} dBFS")
    if st.error:
        state = f"[red]error: {escape(st.error)}[/]"
    elif up is not None and up.uploading:
        state = "[cyan]uploading[/]"
    elif st.state == "speech":
        state = f"[bold green]● speech[/] ({st.prob:.2f})"
    elif st.state == "listening":
        state = "[dim]○ listening[/]"
    else:
        state = st.state
    grid.add_row("state", state)
    grid.add_row(
        "segments",
        f"{st.segments} captured  ·  {st.dropped} too short  ·  {spool.pending()} spooled",
    )
    if up is not None:
        retry = f"  ·  retry in {up.backoff_s:.0f}s" if up.backoff_s else ""
        grid.add_row(
            "uploads", f"{up.uploaded} done  ·  {up.pending} pending  ·  {up.failed} failed{retry}"
        )
        if up.hub_reachable is None:
            hub = escape(hub_note)
        elif up.hub_reachable:
            hub = "[green]reachable[/]"
        else:
            hub = f"[red]unreachable[/] {escape(up.last_error or '')}"
        grid.add_row("hub", hub)
    else:
        grid.add_row("hub", f"[dim]{escape(hub_note)}[/]")
    last = poller.latest() if poller is not None else None
    if last:
        grid.add_row("last", _last_text(last))
    grid.add_row("log", f"[dim]{escape(str(logs_dir() / 'listener.log'))}[/]")
    return Panel(grid, title="soulsaka listen", subtitle="Ctrl-C to stop", border_style="blue")


def _last_text(cap: dict[str, Any]) -> str:
    status = cap.get("status")
    if status == "discarded":
        return f"[yellow]discarded[/] ({escape(str(cap.get('error') or 'other speaker'))})"
    if status != "done":
        return f"[dim]{escape(str(status or 'pending'))}...[/]"
    text = escape((cap.get("text") or "").strip())
    who = cap.get("speaker_is_me")
    tag = "[green]me[/]" if who else ("[yellow]not me[/]" if who is False else "[dim]unverified[/]")
    memories = len(cap.get("memory_uids") or [])
    mem = f"  ·  [bold]{memories} memor{'y' if memories == 1 else 'ies'}[/]" if memories else ""
    return f'"{text}"  ·  {tag}{mem}'
