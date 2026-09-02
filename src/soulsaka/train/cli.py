"""``soulsaka train ...``"""

from __future__ import annotations

import json

import typer
from rich import print as rprint
from rich.table import Table

from soulsaka.config import get_settings

train_app = typer.Typer(
    help="Dataset snapshots, QLoRA retrains, adapter versions.", no_args_is_help=True
)


def _state():
    from soulsaka.hub.state import HubState

    return HubState(get_settings())


@train_app.command("preview")
def preview(n: int = typer.Option(3, help="Sample examples to print.")):
    """What the next training set would look like."""
    from soulsaka.train.dataset import preview as _preview

    state = _state()
    try:
        p = _preview(state.db, state.settings, n=n)
    finally:
        state.close()
    rprint(
        f"[bold]{p['n_examples']:,}[/] training examples, {p['n_words']:,} target words, {p['n_holdout']:,} holdout, {p['conversations']:,} conversations"
    )
    rprint(f"by register: {p['by_register']}   by language: {p['by_lang']}")
    if p["skipped"]:
        rprint(f"skipped: {p['skipped']}")
    for s in p["samples"]:
        rprint("\n[dim]--- system ---[/]")
        print(s["system"])
        for turn in s["context"]:
            rprint(f"[cyan]{turn['role']}[/]: {turn['text']}")
        rprint(f"[green]target[/]: {s['target']}")


@train_app.command("build")
def build(version: str | None = typer.Option(None, help="Version label, default next vN.")):
    """Write the dataset snapshot for a version without training."""
    from soulsaka.train import registry
    from soulsaka.train.dataset import build_snapshot

    state = _state()
    try:
        version = version or registry.next_version(state.db)
        out, m = build_snapshot(state.db, state.settings, version)
    finally:
        state.close()
    rprint(
        f"wrote {out}: {m.n_examples:,} examples, {m.n_words:,} words, {m.n_holdout:,} holdout, cutoff {m.data_cutoff}"
    )


@train_app.command("run")
def run(
    version: str | None = typer.Option(None, help="Version label, default next vN."),
    dry_run: bool = typer.Option(False, help="Build the snapshot and stop."),
    no_export: bool = typer.Option(False, help="Skip GGUF/Modelfile export."),
):
    """Cumulative retrain from the base model on everything so far."""
    from soulsaka.train.runner import plan_version, run_version

    state = _state()
    try:
        run = plan_version(state.db, state.settings, version)
        result = run_version(
            state.db,
            state.settings,
            run["version"],
            dry_run=dry_run,
            echo=print,
            export_gguf=not no_export,
        )
    finally:
        state.close()
    rprint(f"[bold]{result['version']}[/] {result['status']}")
    if result.get("metrics"):
        rprint(result["metrics"])


@train_app.command("list")
def list_runs():
    from soulsaka.train import registry

    state = _state()
    runs = registry.list_runs(state.db)
    state.close()
    t = Table(title="adapters")
    for col in (
        "version",
        "status",
        "backend",
        "examples",
        "words",
        "cutoff",
        "loss",
        "eval",
        "adapter",
    ):
        t.add_column(col)
    for r in runs:
        m = r.get("metrics") or {}
        t.add_row(
            r["version"],
            r["status"],
            r["backend"],
            f"{r.get('n_examples') or 0:,}",
            f"{r.get('n_words') or 0:,}",
            (r.get("data_cutoff") or "")[:10],
            f"{m.get('train_loss'):.3f}" if isinstance(m.get("train_loss"), float) else "",
            f"{m.get('eval_loss'):.3f}" if isinstance(m.get("eval_loss"), float) else "",
            r.get("adapter_path") or (r.get("error") or "")[:40],
        )
    rprint(t)


@train_app.command("serve-llm")
def serve_llm(
    version: str | None = typer.Option(None, help="Adapter version (default: latest done)."),
    port: int | None = typer.Option(None),
    host: str = typer.Option("127.0.0.1"),
    print_only: bool = typer.Option(False, help="Print the command instead of running it."),
):
    """Start llama-server (CUDA) or mlx_lm.server (Apple) with the adapter loaded."""
    from soulsaka.train.serve import serve, serve_command

    state = _state()
    try:
        if print_only:
            print(" ".join(serve_command(state.db, state.settings, version, port=port, host=host)))
            return
        raise typer.Exit(serve(state.db, state.settings, version, port=port, host=host))
    finally:
        state.close()


@train_app.command("export")
def export(version: str, gguf: bool = typer.Option(True), ollama: bool = typer.Option(True)):
    """(Re)create the GGUF LoRA and Ollama Modelfile for a finished version."""
    from pathlib import Path

    from soulsaka.train import registry
    from soulsaka.train.export import convert_lora_to_gguf, write_ollama_modelfile

    state = _state()
    try:
        run = registry.get_run(state.db, version)
        if not run or not run.get("adapter_path"):
            raise typer.BadParameter(f"{version} has no adapter")
        adapter = Path(run["adapter_path"])
        out_dir = adapter.parent
        if gguf:
            p = convert_lora_to_gguf(
                adapter,
                run["base_model"],
                out_dir / f"{version}-lora.gguf",
                state.settings.train,
                log=print,
            )
            registry.update_run(state.db, version, gguf_path=str(p))
            rprint(f"gguf: {p}")
        if ollama:
            p = write_ollama_modelfile(
                adapter,
                state.settings.train.base_gguf or run["base_model"],
                out_dir / "Modelfile",
                f"soulsaka-{version}",
            )
            rprint(f"Modelfile: {p}  (ollama create soulsaka-{version} -f {p})")
    finally:
        state.close()


@train_app.command("show")
def show(version: str):
    from soulsaka.train import registry

    state = _state()
    run = registry.get_run(state.db, version)
    state.close()
    if not run:
        raise typer.BadParameter(f"no run {version}")
    print(json.dumps(run, indent=2, ensure_ascii=False))
