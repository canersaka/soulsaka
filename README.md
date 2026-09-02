# soulsaka

This is my personal AI clone. It learns how I write and how I talk from my own messages and my own voice, it runs entirely on my own computers, and it keeps score of how close each version gets to the real me.

Nothing leaves my machines. That is the whole point.

## What it does

- I can type a note or talk to it from my phone, my laptop or any browser. If I am offline it saves everything and syncs when I am back.
- One computer can keep its microphone on all the time. It only keeps my voice, everyone else's speech is thrown away.
- If I say "remember the locker code is 4521", that becomes a memory on every device within a few seconds. In the background it also pulls useful facts out of normal conversation.
- It imports my message history with one command: iMessage, WhatsApp, email, Discord, git commits, my own writing. It only asks for a login when it really has to.
- It trains a small language model on everything I have written. Texts, emails and speech are tagged separately so it learns that I write differently in each.
- Every month it retrains from scratch on everything so far and saves a new version. Old versions are kept so they can be compared.
- It clones my voice from a short reference clip made out of my own recordings.
- It measures every version. Friends guess which of two replies is the real one, a classifier tries to tell my text from the model's, and the cloned voice is compared to mine. Plotted over versions, that is the result of the project.
- Chat uses the local model by default. I can plug in Claude or OpenAI with an API key if I decide to turn that on.

## Running it

On the computer with the GPU (the hub):

```bash
uv sync --extra hub
soulsaka init --name "Your Name" --email you@example.com --phone "+1 555 000 0000"
soulsaka serve
```

It prints the address to open and a pairing code.

On the Mac that has the messages:

```bash
uv sync
soulsaka hub login --url http://<hub-address>:8765 --code XXXXXXXX
soulsaka import --auto
soulsaka stats
```

On whichever machine should keep its mic on:

```bash
uv sync --extra listener
soulsaka listen
```

On the phone, open the hub address in the browser, enter the pairing code once, and add it to the home screen.

## Commands

`soulsaka serve` runs the hub. `soulsaka import --auto` finds and imports message history. `soulsaka stats` shows how many words of mine are in the corpus. `soulsaka note "..."` is a quick text capture. `soulsaka listen` is the always-on mic. `soulsaka chat "..."` talks to it from the terminal. `soulsaka train run` trains the next version and `soulsaka train serve-llm` serves it. `soulsaka eval pairs`, `eval discriminator`, `eval voice` and `eval report` measure a version. `soulsaka voice reference` builds the voice clip and `soulsaka voice say` speaks. `soulsaka bench` measures latency. `soulsaka --help` lists the rest.

## Docs

- `docs/SETUP.md`: setting up each machine.
- `docs/TRAINING.md`: how training and evaluation work.
- `docs/HOW_IT_WORKS.md`: a walkthrough of every part and how I built it.
- `docs/ROADMAP.md`: what is next.
- `PRIVACY.md`: what is stored and what can leave the machine.

## Development

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest
cd web && npm ci && npm run dev
cd web && npm run test:e2e
```

MIT license.
