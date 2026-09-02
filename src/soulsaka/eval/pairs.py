"""Blind pairs: a real reply of mine next to the model's reply to the same context.

Raters (friends) guess which is real. Accuracy at 50% means the model is
indistinguishable; the number is tracked per adapter version.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

from soulsaka.db import Database
from soulsaka.hub.state import HubState
from soulsaka.ml.llm import ChatMessage
from soulsaka.paths import datasets_dir
from soulsaka.text.normalize import word_count
from soulsaka.train.dataset import iter_examples, read_jsonl
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso

log = logging.getLogger(__name__)


def holdout_examples(state: HubState, version: str) -> list[dict[str, Any]]:
    """Held-out examples for a version: the snapshot's valid.jsonl, or a fresh split."""
    path = datasets_dir() / version / "valid.jsonl"
    if path.exists():
        return list(read_jsonl(path))
    return [ex.__dict__ for ex, holdout in iter_examples(state.db, state.settings) if holdout]


def render_context(messages: list[dict[str, str]], me: str = "Me") -> str:
    lines = []
    for m in messages:
        if m["role"] == "system":
            continue
        who = me if m["role"] == "assistant" else "Them"
        lines.append(f"{who}: {m['content']}")
    return "\n".join(lines)


def _candidates(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for ex in examples:
        msgs = ex["messages"]
        target = msgs[-1]["content"]
        if ex.get("meta", {}).get("n_context", 1) < 1:
            continue
        if not (3 <= word_count(target) <= 80):
            continue
        out.append(ex)
    return out


def generate_pairs(
    state: HubState,
    version: str,
    *,
    n: int = 20,
    profile: str | None = None,
    seed: int | None = None,
) -> list[str]:
    """Ask the model for replies to held-out contexts and store shuffled pairs."""
    examples = _candidates(holdout_examples(state, version))
    if not examples:
        raise RuntimeError(f"no held-out examples for {version}; build the dataset first")
    rng = random.Random(seed if seed is not None else state.settings.train.seed)
    rng.shuffle(examples)
    llm = state.service("llm")
    prof_name, _ = llm.profile(profile)
    uids: list[str] = []
    for ex in examples:
        if len(uids) >= n:
            break
        prompt = [ChatMessage(m["role"], m["content"]) for m in ex["messages"][:-1]]
        try:
            resp = llm.complete(prompt, profile=prof_name, max_tokens=200)
        except Exception as e:  # noqa: BLE001
            log.warning("generation failed: %s", e)
            continue
        model_text = resp.text.strip()
        if not model_text or model_text == ex["messages"][-1]["content"].strip():
            continue
        uid = new_uid()
        with state.db.tx() as conn:
            conn.execute(
                """INSERT INTO eval_pairs(uid, version, context, real_text, model_text, real_first, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    version,
                    render_context(ex["messages"][:-1]),
                    ex["messages"][-1]["content"],
                    model_text,
                    1 if rng.random() < 0.5 else 0,
                    now_iso(),
                ),
            )
        uids.append(uid)
    _record_generation(state.db, version, prof_name, len(uids))
    state.events.publish("eval", version=version, kind="blind_pairs", generated=len(uids))
    return uids


def _record_generation(db: Database, version: str, profile: str, n: int) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO eval_results(version, kind, metric, value, n, details, created_at) VALUES (?, 'blind_pairs', 'generated', ?, ?, ?, ?)",
            (version, float(n), n, json.dumps({"profile": profile}), now_iso()),
        )


def pairs_for_rater(
    db: Database, version: str, rater: str, limit: int = 20
) -> list[dict[str, Any]]:
    rows = db.all(
        """SELECT p.uid, p.context, p.real_text, p.model_text, p.real_first FROM eval_pairs p
           WHERE p.version = ? AND NOT EXISTS (
             SELECT 1 FROM eval_guesses g WHERE g.pair_uid = p.uid AND g.rater = ?)
           ORDER BY p.id LIMIT ?""",
        (version, rater, limit),
    )
    out = []
    for r in rows:
        first, second = (
            (r["real_text"], r["model_text"])
            if r["real_first"]
            else (r["model_text"], r["real_text"])
        )
        out.append({"uid": r["uid"], "context": r["context"], "first": first, "second": second})
    return out


def record_guess(db: Database, uid: str, rater: str, guessed_first: bool) -> bool | None:
    row = db.one("SELECT version, real_first FROM eval_pairs WHERE uid = ?", (uid,))
    if row is None:
        return None
    correct = bool(row["real_first"]) == bool(guessed_first)
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO eval_guesses(pair_uid, rater, guessed_real_is_first, correct, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, rater.strip()[:64], 1 if guessed_first else 0, 1 if correct else 0, now_iso()),
        )
    refresh_blind_summary(db, row["version"])
    return correct


def blind_summary(db: Database, version: str) -> dict[str, Any]:
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(g.correct), 0) AS correct FROM eval_guesses g
           JOIN eval_pairs p ON p.uid = g.pair_uid WHERE p.version = ?""",
        (version,),
    )
    n, correct = int(row["n"]), int(row["correct"])
    raters = {
        r[0]: {"n": r[1], "correct": r[2]}
        for r in db.all(
            """SELECT g.rater, COUNT(*), SUM(g.correct) FROM eval_guesses g
               JOIN eval_pairs p ON p.uid = g.pair_uid WHERE p.version = ? GROUP BY g.rater""",
            (version,),
        )
    }
    pairs = int(db.scalar("SELECT COUNT(*) FROM eval_pairs WHERE version = ?", (version,)) or 0)
    return {
        "version": version,
        "pairs": pairs,
        "n": n,
        "correct": correct,
        "accuracy": (correct / n) if n else None,
        "raters": raters,
    }


def refresh_blind_summary(db: Database, version: str) -> None:
    s = blind_summary(db, version)
    if s["accuracy"] is None:
        return
    with db.tx() as conn:
        conn.execute(
            "DELETE FROM eval_results WHERE version = ? AND kind = 'blind_pairs' AND metric = 'guess_accuracy'",
            (version,),
        )
        conn.execute(
            "INSERT INTO eval_results(version, kind, metric, value, n, details, created_at) VALUES (?, 'blind_pairs', 'guess_accuracy', ?, ?, ?, ?)",
            (version, s["accuracy"], s["n"], json.dumps({"raters": s["raters"]}), now_iso()),
        )


def rater_score(db: Database, version: str, rater: str) -> dict[str, Any]:
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(g.correct), 0) AS correct FROM eval_guesses g
           JOIN eval_pairs p ON p.uid = g.pair_uid WHERE p.version = ? AND g.rater = ?""",
        (version, rater),
    )
    n, correct = int(row["n"]), int(row["correct"])
    return {"rater": rater, "n": n, "correct": correct, "accuracy": (correct / n) if n else None}


def model_texts(db: Database, version: str) -> list[str]:
    return [r[0] for r in db.all("SELECT model_text FROM eval_pairs WHERE version = ?", (version,))]


def export_html(db: Database, version: str, out: Path) -> Path:
    """A standalone page for rating offline; results are shown to the rater to send back."""
    rows = pairs_for_rater(db, version, rater="__export__", limit=10_000)
    payload = json.dumps(rows, ensure_ascii=False)
    html = f"""<!doctype html><meta charset="utf-8"><title>Which one is real? ({version})</title>
<style>body{{font-family:system-ui;max-width:720px;margin:2rem auto;padding:0 1rem}}
.ctx{{white-space:pre-wrap;background:#f4f4f4;padding:1rem;border-radius:8px}}
button{{display:block;width:100%;text-align:left;margin:.5rem 0;padding:1rem;font-size:1rem;border-radius:8px;border:1px solid #ccc;background:#fff;white-space:pre-wrap}}
button:hover{{background:#eef}}</style>
<h1>Which reply is the real one?</h1><p id=prog></p><div class=ctx id=ctx></div><div id=opts></div><h2 id=done></h2>
<script>
const pairs={payload};let i=0;const answers=[];
function show(){{if(i>=pairs.length){{document.getElementById('ctx').textContent='';document.getElementById('opts').innerHTML='';
document.getElementById('done').textContent='Done. Send this back: '+btoa(JSON.stringify(answers));return;}}
const p=pairs[i];document.getElementById('prog').textContent=`Pair ${{i+1}} of ${{pairs.length}}`;
document.getElementById('ctx').textContent=p.context;const o=document.getElementById('opts');o.innerHTML='';
[['first',p.first],['second',p.second]].forEach(([k,t])=>{{const b=document.createElement('button');b.textContent=t;b.onclick=()=>{{answers.push({{uid:p.uid,guessed_first:k==='first'}});i++;show();}};o.appendChild(b);}});}}
show();
</script>"""
    out.write_text(html, encoding="utf-8")
    return out
