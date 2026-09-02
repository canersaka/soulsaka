"""The fidelity curve: one row per adapter version."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from soulsaka.db import Database


def summary(db: Database) -> dict[str, Any]:
    runs = {
        r["version"]: dict(r)
        for r in db.all(
            "SELECT version, finished_at, status, n_words, n_examples FROM training_runs"
        )
    }
    versions: dict[str, dict[str, Any]] = {}
    for v in runs:
        versions[v] = {
            "version": v,
            "trained_at": runs[v]["finished_at"],
            "status": runs[v]["status"],
            "n_words": runs[v]["n_words"],
            "n_examples": runs[v]["n_examples"],
        }
    rows = db.all(
        """SELECT version, kind, metric, value, n, details, created_at FROM eval_results
           WHERE (version, kind, metric, id) IN (
             SELECT version, kind, metric, MAX(id) FROM eval_results GROUP BY version, kind, metric)"""
    )
    for r in rows:
        entry = versions.setdefault(
            r["version"],
            {
                "version": r["version"],
                "trained_at": None,
                "status": None,
                "n_words": None,
                "n_examples": None,
            },
        )
        if r["kind"] == "blind_pairs" and r["metric"] == "guess_accuracy":
            entry["blind_accuracy"] = r["value"]
            entry["blind_n"] = r["n"]
        elif r["kind"] == "blind_pairs" and r["metric"] == "generated":
            entry["pairs_generated"] = int(r["value"])
        elif r["kind"] == "discriminator":
            entry["discriminator_accuracy"] = r["value"]
            entry["discriminator_n"] = r["n"]
        elif r["kind"] == "voice_similarity":
            entry["voice_cosine"] = r["value"]
            with contextlib.suppress(json.JSONDecodeError):
                entry["voice_baseline"] = json.loads(r["details"] or "{}").get("baseline_mean")

    def key(v: str) -> tuple[int, str]:
        return (int(v[1:]), v) if v[1:].isdigit() else (10**9, v)

    ordered = [versions[v] for v in sorted(versions, key=key)]
    for e in ordered:
        for k in (
            "blind_accuracy",
            "blind_n",
            "discriminator_accuracy",
            "discriminator_n",
            "voice_cosine",
            "voice_baseline",
            "pairs_generated",
        ):
            e.setdefault(k, None)
    return {"versions": ordered}


def render_svg(data: dict[str, Any], width: int = 720, height: int = 320) -> str:
    """A dependency-free line chart of the three fidelity signals."""
    vs = data["versions"]
    pad_l, pad_r, pad_t, pad_b = 48, 16, 20, 40
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    n = max(1, len(vs))

    def x(i: int) -> float:
        return pad_l + (w * (i + 0.5) / n)

    def y(v: float) -> float:
        return pad_t + h * (1 - max(0.0, min(1.0, v)))

    series = [
        ("blind_accuracy", "#c0392b", "friends guess right"),
        ("discriminator_accuracy", "#2980b9", "classifier accuracy"),
        ("voice_cosine", "#27ae60", "voice similarity"),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="system-ui" font-size="12">'
    ]
    parts.append(f'<rect width="{width}" height="{height}" fill="white"/>')
    for gv in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(gv):.1f}" y2="{y(gv):.1f}" stroke="{"#999" if gv == 0.5 else "#eee"}" stroke-dasharray="{"4 4" if gv == 0.5 else "0"}"/>'
        )
        parts.append(
            f'<text x="{pad_l - 6}" y="{y(gv) + 4:.1f}" text-anchor="end" fill="#666">{int(gv * 100)}%</text>'
        )
    for i, e in enumerate(vs):
        parts.append(
            f'<text x="{x(i):.1f}" y="{height - pad_b + 16}" text-anchor="middle" fill="#333">{e["version"]}</text>'
        )
    for key, color, _label in series:
        pts = [(x(i), y(e[key])) for i, e in enumerate(vs) if e.get(key) is not None]
        if len(pts) > 1:
            parts.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}"/>'
            )
        for px, py in pts:
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{color}"/>')
    lx = pad_l
    for _key, color, label in series:
        parts.append(f'<rect x="{lx}" y="{height - 14}" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{lx + 14}" y="{height - 5}" fill="#333">{label}</text>')
        lx += 160
    parts.append("</svg>")
    return "\n".join(parts)
