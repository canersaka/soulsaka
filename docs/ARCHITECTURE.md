# Architecture

## Hub and spokes

One process, the **hub** (`soulsaka serve`), owns the data and runs every model. It runs on
the machine with the most compute (the G14 with CUDA, or the M1 Pro MacBook with Metal).
Everything else is a thin **client** that talks to the hub over HTTP on the LAN:

- the **web app** (PWA) on any browser or the iPhone home screen: capture, chat,
  memories, sources, dashboard. Keeps an IndexedDB queue so captures work offline and
  upload when the hub is reachable again.
- the **listener** (`soulsaka listen`): always-on microphone with local voice activity
  detection. Segments are spooled to disk and uploaded with retries.
- the **importers** (`soulsaka import`): run where the data is (the Mac for iMessage and
  WhatsApp) and push batches to the hub, or write straight into the database when run on
  the hub itself.

Clients identify themselves with a bearer token obtained once from a pairing code.

## Data flow

```
capture (text|audio) ─▶ jobs.process_capture
    audio: speaker verify ─▶ (other? discard/context) ─▶ ASR ─▶ transcript
    ─▶ messages (is_me=1, register=text|speech, origin=manual|listener|chat)
    ─▶ rule memories ("remember ...")  ─▶ SSE event ─▶ every open client
    ─▶ jobs.extract_memories_llm (durable facts)   ─▶ memories
    ─▶ jobs.embed_message / embed_memory           ─▶ embeddings

importer ─▶ /api/messages/batch ─▶ messages (is_me from source, others hashed)

chat ─▶ retrieval (FTS5 + cosine, RRF) ─▶ prompt (self-model, memories, exemplars,
        register hint) ─▶ LLM profile (local adapter | cloud) ─▶ stream

train ─▶ dataset snapshot (my turns as targets, N prior turns as context, register
        tags, holdout by conversation, content hash) ─▶ QLoRA from base ─▶ adapters/vN
        ─▶ GGUF / Modelfile ─▶ llama.cpp | Ollama

eval ─▶ blind pairs (holdout context: real vs model) ─▶ friends guess ─▶ accuracy/vN
     ─▶ discriminator (TF-IDF + LR) accuracy/vN ─▶ voice ECAPA cosine/vN ─▶ dashboard
```

## Storage

SQLite in WAL mode with FTS5, one file. Integer primary keys; client-created rows carry
a `uid` for idempotent sync. Embeddings are float32 blobs searched by brute-force cosine
(fine to a few hundred thousand rows). Migrations are numbered SQL files applied at open.

Key tables: `sources`, `contacts` (hashed handles), `conversations`, `messages`,
`captures`, `memories`, `embeddings`, `speaker_profiles`, `jobs`, `chats`/`chat_turns`,
`training_runs`, `eval_results`, `eval_pairs`/`eval_guesses`.

## Jobs

A durable queue in the `jobs` table with an in-process worker thread. One worker by
default so only one copy of each model is resident. Failures retry with exponential
backoff; jobs left running by a crash are re-queued at startup.

## Registers

Every message carries a register: `text` (chat apps), `email`, `speech` (transcribed),
`doc` (writing). Training samples are tagged with their register so the model can be
asked for either voice; chat prompts carry a register hint.

## Model backends

`ml/llm.py` routes to named profiles: OpenAI-compatible servers (llama.cpp, Ollama, LM
Studio, vLLM, OpenAI), the Anthropic Messages API, or a local CLI. `ml/asr.py` wraps
faster-whisper (CUDA/CPU) and mlx-whisper (Apple Silicon). `ml/speaker.py` wraps
SpeechBrain ECAPA-TDNN. Every backend has a fake so the test suite runs without a GPU.

## Multi-device "remember this"

Listener hears "remember the wifi password is ..." ─▶ segment uploaded ─▶ verified as
me ─▶ transcribed ─▶ rule extractor stores a memory ─▶ `memory` event on `/api/events`
─▶ the phone's open tab renders it; a closed tab catches up through `/api/sync?since=`.
