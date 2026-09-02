"""``soulsaka eval ...``"""

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from soulsaka.config import get_settings

eval_app = typer.Typer(help="Fidelity evals per adapter version.", no_args_is_help=True)


def _state():
    from soulsaka.hub.state import HubState

    return HubState(get_settings())


@eval_app.command("pairs")
def pairs(
    version: str = typer.Option(
        ..., help="Adapter version being evaluated (its dataset snapshot must exist)."
    ),
    n: int = typer.Option(20),
    profile: str | None = typer.Option(None, help="LLM profile serving that adapter."),
):
    """Generate blind pairs from held-out contexts."""
    from soulsaka.eval.pairs import generate_pairs

    state = _state()
    try:
        uids = generate_pairs(state, version, n=n, profile=profile)
    finally:
        state.close()
    rprint(f"generated {len(uids)} pairs for {version}; friends can rate at <hub>/rate/{version}")


@eval_app.command("rate")
def rate(version: str = typer.Option(...), rater: str = typer.Option("me")):
    """Rate pairs in the terminal."""
    from soulsaka.eval.pairs import pairs_for_rater, rater_score, record_guess

    state = _state()
    try:
        for p in pairs_for_rater(state.db, version, rater, limit=100):
            rprint("\n[dim]" + p["context"].replace("[", "\\[") + "[/]")
            rprint("[bold]1.[/] " + p["first"].replace("[", "\\["))
            rprint("[bold]2.[/] " + p["second"].replace("[", "\\["))
            choice = typer.prompt("which is real? (1/2, q to stop)")
            if choice.lower().startswith("q"):
                break
            correct = record_guess(state.db, p["uid"], rater, choice.strip() == "1")
            rprint("[green]right[/]" if correct else "[red]wrong[/]")
        rprint(rater_score(state.db, version, rater))
    finally:
        state.close()


@eval_app.command("discriminator")
def discriminator(version: str = typer.Option(...)):
    """Train a real-vs-model classifier on this version's pairs and report CV accuracy."""
    from soulsaka.eval.discriminator import run_discriminator

    state = _state()
    try:
        rprint(run_discriminator(state, version))
    finally:
        state.close()


@eval_app.command("voice")
def voice(version: str = typer.Option(...), n: int = typer.Option(8)):
    """Speaker-embedding similarity between the cloned voice and yours."""
    from soulsaka.eval.voice import run_voice_similarity

    state = _state()
    try:
        rprint(run_voice_similarity(state, version, n=n))
    finally:
        state.close()


@eval_app.command("report")
def report(svg: Path | None = typer.Option(None, help="Also write the fidelity chart as SVG.")):
    from soulsaka.eval.report import render_svg, summary

    state = _state()
    data = summary(state.db)
    state.close()
    t = Table(title="fidelity by version")
    for col in (
        "version",
        "trained",
        "words",
        "friends guess",
        "n",
        "classifier",
        "voice cos",
        "baseline",
    ):
        t.add_column(col)
    for e in data["versions"]:
        f = lambda v: "" if v is None else f"{v:.2f}"  # noqa: E731
        t.add_row(
            e["version"],
            (e.get("trained_at") or "")[:10],
            f"{e.get('n_words') or 0:,}",
            f(e.get("blind_accuracy")),
            str(e.get("blind_n") or ""),
            f(e.get("discriminator_accuracy")),
            f(e.get("voice_cosine")),
            f(e.get("voice_baseline")),
        )
    rprint(t)
    if svg:
        svg.write_text(render_svg(data), encoding="utf-8")
        rprint(f"wrote {svg}")


@eval_app.command("export-html")
def export_html_cmd(version: str = typer.Option(...), out: Path = typer.Option(Path("rate.html"))):
    """A standalone rating page to send to friends who cannot reach the hub."""
    from soulsaka.eval.pairs import export_html

    state = _state()
    try:
        rprint(f"wrote {export_html(state.db, version, out)}")
    finally:
        state.close()
