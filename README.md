# soulsaka

A private clone of how you write and speak, built from your own messages and voice,
trained and served on your own hardware. Nothing leaves your machines.

soulsaka is a **continual-personalization pipeline**: it captures what you say and write,
keeps a searchable memory of what matters, retrains a small language model on the
accumulated corpus on a schedule, clones your voice, and measures how close each version
gets to the real you. The measurement is the point: a fidelity curve across adapter
versions is the deliverable, not a chatbot.

```
              ┌────────────── hub (G14 or MacBook) ──────────────┐
  Mac/iPhone  │  ASR (Whisper)  ─▶ who is talking? (ECAPA)        │
  browser  ──▶│  ▶ corpus (SQLite)  ▶ memories  ▶ retrieval       │◀── any device:
  listener ──▶│  ▶ dataset builder ▶ QLoRA retrain vN ▶ llama.cpp  │    chat, memories,
  importers──▶│  ▶ TTS in your voice ▶ evals: fidelity vs version  │    sources, dashboard
              └───────────────────────────────────────────────────┘
```

## What it does

- **Capture** on any device: typed notes, push-to-talk, or an always-on microphone.
  Clients work offline and sync when they are back on the network.
- **Speaker verification**: the listener only keeps *your* voice. Other people's speech
  is discarded by default (or kept as context, never as training data).
- **Memories**: say "remember the locker code is 4521" and it is on every device within
  seconds; an LLM pass extracts durable facts from ordinary speech in the background.
- **Import your history** with one command: iMessage and WhatsApp databases are found
  automatically on a Mac, email needs a login, exports can be dropped in.
- **Register-aware training**: texts, emails and speech are tagged so the model learns
  that you write differently in each, and only *your* turns are ever training targets.
- **Cumulative retrains** from the base model into versioned adapters (v1, v2, ...),
  never incremental fine-tunes, so every version is reproducible and comparable.
- **Voice**: zero-shot cloning from a one-minute reference clip on day one; fine-tuning
  once enough clean audio has accumulated.
- **Evals**: blind pairs for friends, a discriminator classifier whose accuracy should
  fall toward 50%, and speaker-embedding similarity for the voice. All plotted per version.
- **Model choice**: your local adapter (llama.cpp / Ollama) by default; cloud models via
  API key only if you flip an explicit privacy switch.

## Hardware

| Role | Machine | Notes |
| --- | --- | --- |
| Hub (training + serving) | ROG Zephyrus G14 (Ryzen AI 9 HX 370, RTX 5070 Ti) | CUDA: Unsloth QLoRA, faster-whisper, llama.cpp |
| Hub (alternative) | MacBook Pro M1 Pro | Metal/MLX: mlx-lm LoRA, mlx-whisper, llama.cpp |
| Importer + listener | MacBook | iMessage and WhatsApp databases live here |
| Capture + chat | iPhone, any browser | PWA; offline queue syncs on reconnect |

## Quick start

```bash
# on the hub machine (the G14 in WSL2, or the MacBook)
uv sync --extra hub            # or: pip install -e ".[hub]"
soulsaka init --name "Your Name" --email you@example.com --phone "+1 617 555 0199"
soulsaka serve                   # prints the URLs to open and a pairing code

# on the Mac with your messages
uv sync
soulsaka hub login --url http://<hub-ip>:8765 --code XXXXXXXX
soulsaka import --auto           # finds iMessage / WhatsApp / Mail / git, asks only when it must
soulsaka stats                   # "the number": words of you in the corpus

# always-on microphone on whichever machine wears it
uv sync --extra listener
soulsaka listen
```

Open the hub URL on your phone, enter the pairing code once, and add it to the home
screen. See [docs/SETUP.md](docs/SETUP.md) for the per-machine setup (Windows + WSL2 on
the G14, the Mac, the iPhone), [docs/TRAINING.md](docs/TRAINING.md) for the retrain and
eval loop, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit, and
[docs/ROADMAP.md](docs/ROADMAP.md) for what comes next. [PRIVACY.md](PRIVACY.md) explains
exactly what is stored and what can leave the machine.

## Commands

| Command | What it does |
| --- | --- |
| `soulsaka serve` | Run the hub: API, web app, background workers |
| `soulsaka import --auto` | Find and import iMessage, WhatsApp, Apple Mail, git; `import imap`, `import whatsapp-export`, `import discord`, `import docs` for the rest |
| `soulsaka stats` | Words of you by register, source, language and month |
| `soulsaka note "remember ..."` | Quick text capture (rule-based memories land instantly) |
| `soulsaka listen` | Always-on microphone with VAD, offline spool and upload |
| `soulsaka chat "..."` | One-shot chat; `--mode twin` answers as you |
| `soulsaka self-model --regenerate` | Rebuild the style fingerprint + profile that goes into every prompt |
| `soulsaka train preview / run / list / serve-llm` | Snapshot, cumulative QLoRA retrain, versions, serve the adapter |
| `soulsaka eval pairs / discriminator / voice / report` | Blind pairs for friends, classifier proxy, voice similarity, the curve |
| `soulsaka voice reference / say / dataset` | Build the TTS reference clip, speak, export a fine-tuning set |
| `soulsaka bench` | Capture-to-memory and chat latency against a running hub |
| `soulsaka pair`, `soulsaka devices`, `soulsaka hub login` | Pairing and devices |

## Layout

```
src/soulsaka/
  cli.py          soulsaka command
  config.py       settings (config.toml + SOULSAKA_* env)
  db/             SQLite schema, migrations, data access
  importers/      iMessage, WhatsApp, mail, Discord, git, docs, auto-discovery
  hub/            FastAPI app, auth/pairing, job queue, capture pipeline, chat, routes
  ml/             ASR, speaker verification, embeddings, LLM backends
  listener/       always-on microphone client
  train/          dataset builder, QLoRA backends, adapter registry, export, serving
  eval/           blind pairs, discriminator, voice similarity, report
  voice/          TTS, reference clip, fine-tuning dataset
  bench.py        latency measurements
web/              PWA (Vite + Preact)
scripts/          per-machine setup, service files, monthly retrain
tests/            pytest suite; ML backends have fakes so it runs without a GPU
```

## Development

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest
cd web && npm ci && npm run dev    # UI against a hub on :8765
```

MIT licensed.
