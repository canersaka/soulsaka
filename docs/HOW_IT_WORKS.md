# How it works

This is the long version. For each part of the system: what it does, how it actually works, why I built it that way, which files to open, and what broke while building it. Read it once top to bottom, then use it as an index.

## The idea in one paragraph

My devices are spokes and one machine is the hub. The spokes capture things: typed notes, push-to-talk clips, an always-on microphone, exports of my message history. They send everything to the hub over HTTP. The hub owns the one database file and runs every model: speech recognition, speaker verification, embeddings, the language model, text-to-speech. It turns captures into three things. The corpus, which is what I said, tagged by register. Memories, which are facts the assistant should know. And training snapshots, which are chat examples where my reply is the target. A retrain from the base model produces adapter v1, v2, v3 and so on, and the evals measure each one against conversations it never saw, so I get a curve instead of a feeling. Nothing leaves my machines unless I flip one switch.

## How I organised the work

The order mattered as much as the code.

I built the base first, on its own: the package layout, config, database schema, auth, the job queue, the capture pipeline, the CLI, tests and CI. Everything else depends on those conventions.

Then I built the three big independent pieces against fixed interfaces: the importers (given the message model and the ingest function), the web app (given the exact API routes and JSON shapes), and the listener (given the client upload call and the spool folder). They only touch their own folders, so they could be built separately and integrated at the end.

Every model has a fake with the same interface. The whole system, including the browser tests, runs without a GPU. That is what made building the pieces separately and running CI possible. It also means the first run on real hardware is still ahead.

I committed small and pushed often. CI went red a few times and every time it was something environmental, listed at the end.

## Repository and tooling

Files: `pyproject.toml`, `.github/workflows/ci.yml`, `requirements/`.

uv manages the Python project. `uv sync --group dev` creates the environment with the base dependencies plus test tools. `--extra hub` adds the model backends. `--extra listener` adds the microphone stack. The lock file is universal, so it resolves for Linux, macOS and Windows at once.

The training and voice stacks are on purpose not extras. Unsloth and F5-TTS pin their own torch builds, and putting them in the lock would either fail or force one CUDA version on everyone. They live in `requirements/train.txt`, `train-mlx.txt` and `voice.txt` and get installed on the machine that needs them.

The code is in `src/soulsaka/` so tests import the installed package and not the working directory by accident. ruff does lint and formatting. CI runs lint, format check and the tests, then builds the web app.

Typer for the CLI, FastAPI for the hub, pydantic for every data shape that crosses a boundary. The optional CLI groups (import, train, eval, listen) are mounted only if their module imports, and a broken one prints a warning instead of taking down `soulsaka serve`.

## Configuration

File: `src/soulsaka/config.py`.

Settings are pydantic-settings models. Priority, highest first: explicit arguments, environment variables (`SOULSAKA_SECTION__KEY`, double underscore for nesting), then `~/.soulsaka/config.toml`, then defaults. The TOML source is inserted at load time because the data folder itself comes from an environment variable.

The sections are `hub`, `me` (my names, emails and phones, used to decide which messages are mine), `llm`, `asr`, `speaker`, `embed`, `privacy`, `train`, `tts` and `listener`.

The one clever bit is LLM profiles. `llm.profiles` is a dictionary of named endpoints. A validator merges my profiles over the built-in ones, so defining only `[llm.profiles.local]` in the config file does not delete the `claude`, `openai`, `ollama` and CLI profiles. Without that merge the first edit to one profile silently wiped all the others. A test caught it.

## Storage

Files: `src/soulsaka/db/connection.py`, `db/migrations/0001_init.sql`, and the `db/*.py` modules.

One SQLite file in WAL mode. WAL lets many readers work while one writer commits.

Connections are per thread, because SQLite connections must not be shared between threads. There is one lock around write transactions so the API threads, the job worker and the CLI never fight. A write transaction starts with `BEGIN IMMEDIATE`, which takes the write lock up front instead of at the first write, and that avoids a whole class of busy errors.

Migrations are numbered SQL files inside the package, applied when the database opens. One catch: Python's `executescript` commits any open transaction first, so the migrator puts `BEGIN` and `COMMIT` inside the script text itself, together with the row that records the migration, which makes each one atomic.

Every table uses an integer primary key, and rows that clients create carry a text `uid` as well. The integer key is needed because full-text search tables need a stable row id. The uid is needed because the phone and the listener create rows while offline and have to be able to re-send them safely. Re-sending the same uid is a no-op.

Full-text search is FTS5 on messages and memories, kept in sync by triggers. The tokenizer strips diacritics so `hatirla` matches `hatırla`.

Dedup is a unique constraint on the conversation plus a hash of timestamp and text. Re-importing the same export inserts nothing.

Vectors are float32 blobs. Search is a brute-force dot product in numpy. At a few hundred thousand rows that is milliseconds and needs no extension.

## Identity and privacy

Files: `src/soulsaka/identity.py`, `PRIVACY.md`, `tests/test_egress.py`, `src/soulsaka/hub/auth.py`.

Other people's phone numbers, emails and usernames are hashed with a random per-install salt before they touch the database. The same person always hashes to the same contact, so conversations stay coherent, but the database cannot be joined against a phone book.

Which messages are mine: iMessage and WhatsApp say so directly. Exports and email are matched against the names, emails and phones in my config.

Audio that is not my voice is deleted and no transcript is kept, unless I change the policy to keep transcripts as context.

There is a test that scans every source file for URLs and fails if a host is not local or on a short allowlist. Another test checks that every built-in profile pointing off my network is marked cloud. At runtime the model router refuses cloud profiles unless the switch is on, and refuses a profile marked local whose address is not local.

Requests from the hub machine itself are trusted without a token only if they carry a custom header. A custom header forces a browser preflight, and the hub only allows the dev server origins, so a random website cannot read my memories through my own browser. Device tokens are stored hashed and come from short-lived pairing codes.

## The hub

Files: `src/soulsaka/hub/app.py`, `state.py`, `auth.py`, `events.py`, `routes/`.

`create_app` builds the FastAPI app. A startup hook starts the job worker and a shutdown hook stops it. Tests skip the worker and run jobs by hand. `HubState` bundles the settings, the database, the salt, the event bus and a lazy service registry, so heavy models are only imported when first used and tests can inject fakes.

Pairing: `soulsaka serve` and `soulsaka pair` mint an 8-character code. `POST /api/pair` redeems it once and returns a token.

Events: an in-process bus with one queue per subscriber. Workers publish from threads, the server-sent events endpoint drains its queue every quarter second and sends a keepalive every 15 seconds. A slow client just misses events and catches up through `/api/sync?since=`.

The web app is served by the hub itself. Real files are served by name and anything else falls back to `index.html`, so client-side routes survive a refresh.

## Background jobs

Files: `src/soulsaka/db/jobs.py`, `src/soulsaka/hub/jobs.py`.

The queue is a table, not an in-memory list, so a crash loses nothing. Claiming a job is one write transaction that picks the highest-priority runnable row and marks it running. Failures retry with exponential backoff until the attempts run out. On startup, jobs left running by a dead process are put back. One worker thread by default, so only one copy of each model sits in memory.

Training runs in a child process so the hub keeps serving and the GPU memory is freed when it finishes.

## The capture pipeline

Files: `src/soulsaka/hub/routes/captures.py`, `hub/services/pipeline.py`, `memory_extract.py`, `extract_llm.py`, `speaker_enroll.py`, `src/soulsaka/ml/audio.py`.

Text captures arrive as JSON, audio as a file upload. Every clip is converted to 16 kHz mono WAV, trying soundfile, then PyAV, then the ffmpeg binary. The row is created as pending, a job is queued, and an event goes out so open apps show it right away.

For audio the job does this, in order:

1. Speaker check first. If a voice profile exists with enough samples, the clip is embedded and compared to it. Not me, and the policy is discard: delete the audio and stop. Cheap, and it means transcription never runs on someone else.
2. Transcribe. Empty text means the clip is discarded as no speech.
3. Store the transcript as a message with register `speech`, so training and retrieval see it like any other message.
4. Rule-based memories. A small set of patterns catches explicit requests in English and Turkish: remember, don't forget, hatırla, not al, unutma. The body is classified as a number, event, todo or note. This is the zero-latency path. The memory exists and the event fires before any model runs.
5. Queue the slow work: a language model pass over the utterance and an embedding job.
6. Bootstrap the voice profile. If nobody is enrolled yet and the clip came from push-to-talk, which by definition is me, queue an enrolment. After three clips the speaker check switches on. The always-on listener never enrols, because its clips are not guaranteed to be me.

The language model pass asks for durable, specific, third-person facts as JSON. Candidates are compared to existing memories by word overlap so the same fact is not stored twice. If no model is reachable, the job just returns, so a hub without a model server still does everything else.

## Model backends

Files under `src/soulsaka/ml/`.

Speech recognition: faster-whisper (float16 on CUDA, int8 on CPU, with its voice activity filter on, and segments with a high no-speech probability dropped because Whisper makes things up on silence), mlx-whisper for a Mac, and a fake for tests. Language is detected per clip unless configured, which is what handles switching between Turkish and English.

Speaker verification: ECAPA-TDNN embeddings from SpeechBrain, normalised to unit length. The profile is a running average that improves as I talk. A clip is me if its cosine similarity to the profile is above the threshold (0.55 by default; same-speaker scores are usually 0.6 to 0.8, different speakers under 0.3). The fake backend makes a fixed vector from a label so tests can script "me, me, me, stranger".

Embeddings: sentence-transformers by default, an OpenAI-compatible endpoint as an option, and a hashing embedder that needs no dependencies. The hub falls back to the hashing one with a warning if sentence-transformers is missing, so retrieval always works.

The model router has three backends behind one interface. OpenAI-compatible, which covers llama.cpp, Ollama, LM Studio, vLLM and the OpenAI API, with streaming. The Anthropic messages API, also streaming. And a command backend that runs a local program with the conversation on standard input, which is how the official `claude` and `codex` tools are wired. API keys come from an environment variable named in the profile so they stay out of the config file.

## Retrieval and chat

Files: `src/soulsaka/hub/services/retrieval.py`, `chat.py`, `src/soulsaka/train/prompting.py`.

Search is hybrid. Full-text hits and vector hits are combined with reciprocal rank fusion: each result gets 1 divided by 60 plus its rank in each list, summed. That avoids having to compare a BM25 score with a cosine score, which are not on the same scale.

Two searches feed the prompt: memories, and my own past messages that resemble the question, which act as style examples.

Chat has two modes. Assistant mode is a plain assistant that knows my memories. Twin mode answers as me, and it uses the exact same system prompt function that the training data uses, with the register, the language and the setting. That is the point of having one function: the adapter sees the same framing at inference that it saw in training. If the profile has no adapter, for example a cloud model, an extra line tells it to lean on the examples.

My side of every chat is also saved as a capture, marked as chat, so it counts as my writing but training can leave it out.

## The self-model

File: `src/soulsaka/hub/services/self_model.py`.

Two parts, written to `self_model.md`. A style fingerprint computed without any model: median words per text, how often I start lowercase, end with punctuation, use emoji, my most used words, language mix, when I am active. And a narrative written by the language model from recent memories and a sample of my messages: who I am, what I care about, how I write and argue, what I would never say. The narrative is skipped if no model is reachable. It gets regenerated after every training run.

## Importers

Files under `src/soulsaka/importers/`. Fixtures in `tests/fixtures/importers.py`.

Every importer yields message objects and never touches the database. A sink consumes them in chunks, either straight into the database on the hub machine or over the API from another device. Nothing is loaded whole, a huge mailbox streams.

iMessage: copies the database and its journal files first, because Messages keeps it open, then reads it read-only. Skips system rows, reactions and app messages. Dates are nanoseconds since 2001 on modern macOS and seconds on old ones, decided by their size. When the text column is empty the body is inside a binary blob: the parser finds the string marker bytes, reads a length that can be one, two or four bytes, and decodes that many bytes as UTF-8. If macOS blocks the read, discovery explains Full Disk Access instead of crashing.

WhatsApp Desktop: the app's SQLite database, text messages only, with its own "from me" flag.

WhatsApp exports: one pattern for the iOS format and one for Android, tolerant of 24-hour times, Turkish AM/PM, dotted dates and invisible direction marks. Continuation lines attach to the previous message. Day-first or month-first is decided by scanning every date in the file. System notices are dropped and media placeholders are normalised so they count as skipped. Who is me is matched by name, or given with a flag, and if it is unknown the error lists the names it saw. Takes a file, a folder or a zip.

Email: plain text preferred, HTML reduced to text otherwise. Cleaning removes quoted lines, "On ... wrote" blocks in a few languages, everything after a signature separator, forwarded headers and mailing-list footers. Threads are keyed by Gmail's thread id when present, otherwise by the subject with Re and Fwd stripped. Only threads where I wrote something are imported, and automated senders are skipped. Apple Mail's files are a byte count line, the message, then a plist. IMAP fetches the sent folder with a prompted password.

Discord data packages, git commit messages by my email, and text files split into chunks are the remaining sources.

Discovery asks every importer for its known locations on the current OS, checks that they can be read, estimates counts cheaply, and prints a table before importing. Files dropped on the web app go through the same importers after their type is sniffed.

## The always-on listener

Files under `src/soulsaka/listener/`.

Audio comes in at 16 kHz in blocks of 512 samples, which is the frame size the silero voice detector expects. The audio library is imported only where the stream opens, so tests never need it.

Voice detection is silero when installed and otherwise an energy detector that compares loudness against an adaptive noise floor. Both give a probability per frame.

The segmenter is a small state machine: a pre-roll buffer so the first syllable is not cut, a segment starts after enough speech, ends after 800 ms of silence, splits at 30 seconds, and blips are dropped. It is pure numpy and fully tested with synthetic tone bursts.

Segments are written to a spool folder as a WAV plus a JSON sidecar, through temporary files and a rename, sidecar last, so a crash never leaves a half-written pair that looks complete. A size cap deletes the oldest.

An uploader thread sends the oldest entry, deletes it on success, and backs off from 1 to 60 seconds on failure while keeping the files. That backoff is the offline buffer. Rejections the hub will never accept are moved to a failed folder instead of blocking the queue.

The listener never decides whether a voice is mine. It sends every speech segment and the hub applies the speaker profile and the privacy policy. Keeping that decision in one place is what makes it auditable.

## The web app

Files under `web/src/`.

Vite, Preact and TypeScript in strict mode, hand-written CSS with light and dark themes, a service worker so it installs as an app. Sidebar on wide screens, tab bar on phones.

`api.ts` is one fetch wrapper that adds the client header and the token and switches to the pairing screen on a 401. `queue.ts` is the offline queue: captures go into IndexedDB first with a client id, and one flush routine uploads them when the network comes back, when the tab becomes visible, after each new item and every 30 seconds. `sse.ts` reads the event stream through fetch, because the built-in EventSource cannot send headers, and calls sync on every reconnect to catch up.

Push-to-talk uses MediaRecorder. Always-listening in the browser uses an analyser node with a loudness threshold, a short hangover and a pre-roll, and uploads WAV segments. The pages are Capture, Chat, Memories, Corpus, Train, Rate and Settings.

The browser tests start two real hubs with fake models plus a fake model server and drive the app with a fake microphone. Nine scenarios, including going offline and back.

## Training

Files under `src/soulsaka/train/`. The rules are also in `TRAINING.md`.

The dataset builder is the heart of the project. For every conversation with at least one message of mine: messages from one side within 20 minutes are merged into one turn; every turn of mine with two to four hundred words becomes a target; the context is up to eight prior turns, none older than three days, trimmed to fit; the system prompt names the register, the language and the setting; things with no partner get a short instruction; duplicates are dropped; and 5 percent of conversations are held out by a hash of their id, so the split is the same in every version.

The snapshot is three JSONL files plus a manifest with counts, the cutoff date, the config and a hash of the training file. The same corpus always produces the same hash.

Three backends write an adapter folder and a metrics file. Unsloth on CUDA: 4-bit base, LoRA rank 16 on attention and MLP, loss only on the reply. Plain PEFT with the same recipe for machines without Unsloth. mlx-lm on a Mac, run as a subprocess so its memory is freed afterwards.

The registry records version, backend, base model, dataset hash, cutoff, counts, paths and metrics. Export turns the adapter into a small LoRA file that llama.cpp applies at load time, and writes an Ollama Modelfile. `train serve-llm` builds the right server command from the registry.

## Evals

Files under `src/soulsaka/eval/`.

Blind pairs: held-out contexts with some prior turns and a reply of 3 to 80 words; the model writes its reply; the pair is stored in random order; raters pick; accuracy per version is recomputed on every guess. The rating endpoints need no token so a link works for friends.

Discriminator: a classifier trained to tell my replies from the model's on that version's pairs, scored with 5-fold cross-validation. With scikit-learn it is TF-IDF over words and character n-grams into logistic regression; without it, hashed features into a small numpy logistic regression, which is what CI runs. If accuracy goes up on a new version, that version is over-trained.

Voice: a few of my sentences synthesised, embedded with the speaker model, compared to my profile, with a baseline from real clips for reference.

The report joins the training runs with the latest result of each kind and draws a chart with the 50 percent line.

## Voice

Files under `src/soulsaka/voice/`.

Zero-shot voice models want 6 to 12 seconds of clean speech with its exact transcript. The reference is assembled from the best verified clips, concatenated with short gaps until it is long enough, with the transcripts joined. Text-to-speech wraps F5-TTS behind a small interface with a fake for tests. There is an export of every verified clip in the format the fine-tuning tools expect, for when an hour of audio has accumulated.

## Measuring latency

File: `src/soulsaka/bench.py`. `soulsaka bench` sends captures and waits until the hub reports them done, timing text to memory and audio to transcript end to end, and streams chat prompts timing the first token and the throughput. The percentiles go into a JSON report so changes can be compared against the same yardstick.

## Testing

Fakes at the model boundary, real everything else. The tests run the real API through Starlette's test client, the real SQLite file, the real job runner drained by hand, and fake speech, speaker, embedding and voice backends. The language model tests use the command backend pointed at a one-line Python script, so "the model said X" is scripted without mocking HTTP.

The importer fixtures mimic the real formats: a synthetic Messages database with the real tables and hand-built binary blobs, a WhatsApp database, iOS and Android exports in English and Turkish, a mailbox with quoted replies and a newsletter, an Apple Mail tree, a Discord package, and a real temporary git repository.

## Things that broke

- The event bus took the event name as its first argument and payloads that had a `kind` field collided with it. Fixed by making the name positional-only.
- pydantic warned about a field named `register` because its base class inherits a method with that name. Harmless, filtered once with a comment.
- Overriding one model profile through the environment wiped the built-in ones. Fixed with the merge validator.
- A 4-digit number did not count as a number memory because the pattern demanded five characters. Test with the shortest realistic input.
- Identical test conversations were collapsed by the deduplicator, so counts came out low. The deduplicator was right, the fixtures had to vary.
- A float32 cosine came out as 1.0000001 and failed a check against 1.0. Clamp.
- GitHub Actions does not allow `hashFiles` in a job-level condition and the workflow failed to even start. Detect in a step and gate the later steps.
- Rich colours help text on CI runners, so a test that looked for `--device` in the output failed there and passed locally. Strip the colour codes in CLI tests.
- Killing the server with `pkill -f` and the server's command line as the pattern killed the shell running the command too. Use the port instead.
- Playwright's network-idle wait never fires on a page holding an open event stream. Wait for load plus a short delay.
- `executescript` commits any open transaction. Put the transaction inside the script.

## Extending it

A new importer: subclass `Importer`, set `kind`, yield messages from `iter_messages`, implement `discover` for its known paths, register it, add a CLI command and a fixture-based test. Sinks, dedup, hashing and stats come for free.

A new model backend: add a branch in the router and decide whether it is cloud. The egress test tells you if you forgot.

A new background job: write a handler, register it, enqueue it. Retries and backoff are automatic.

A new eval: write a row into the results table with a version, a kind, a metric and a value, and add the kind to the report.

A new speech or speaker backend, for example on an NPU: implement the small protocol in `ml/asr.py` or `ml/speaker.py`, add a backend name to the config, and select it in the service builder. Nothing else changes.
