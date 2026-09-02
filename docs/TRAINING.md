# Training and evaluation

## The loop

```
corpus grows daily  ──▶  soulsaka train run  ──▶  adapters/vN  ──▶  soulsaka train serve-llm
                          (snapshot + QLoRA        │
                           from the base model)    └──▶ soulsaka eval pairs / discriminator / voice
                                                        └──▶ soulsaka eval report  (the curve)
```

Every version is a **cumulative retrain from the base model** on everything up to its
cutoff date. Incremental fine-tunes drift and forget; cumulative retrains are
reproducible (the snapshot's hash is recorded) and directly comparable, which is what
makes the fidelity curve meaningful.

## What a training example looks like

```json
{"messages": [
  {"role": "system", "content": "You are Caner. Write exactly as Caner would ...\nRegister: text. Text messages: short, casual ...\nLanguage: Turkish ...\nSetting: 1:1 whatsapp conversation with Ali"},
  {"role": "user", "content": "bu akşam gelir misin"},
  {"role": "assistant", "content": "gelirim ya, saat 8 gibi"}
 ],
 "meta": {"conversation_id": 12, "register": "text", "lang": "tr", "ts": "...", "n_context": 1, "source": "whatsapp"}}
```

Rules (see `src/soulsaka/train/dataset.py`):

- Only **my** messages are targets. Other people's messages appear only inside user
  turns, as context. Group chats prefix each context line with the sender's name.
- Consecutive messages from one side within 20 minutes are merged into one turn, so the
  model learns my bursts ("yeah" / "probably around 8") as one reply.
- Up to `train.context_window` prior turns, no older than three days, trimmed to fit
  `max_seq_len`.
- Register (`text`, `email`, `speech`, `doc`) and language are in the system prompt, so
  the same adapter can be asked for either voice. Chat with the assistant itself is
  excluded by default (`train.include_chat_turns`): talking to a bot is a narrow register.
- Material without a partner (commit messages, documents, notes) gets a short
  instruction as the user turn.
- Media placeholders, URLs-only and one-word replies are dropped; exact duplicates are
  dropped; each conversation contributes at most `train.max_per_conversation`.
- 5% of **conversations** are held out (by hash, so the split is stable across
  versions) and written to `valid.jsonl`. Evals only ever use held-out conversations.

`soulsaka train preview` prints the counts and a few rendered examples before you spend
GPU time.

## Backends

| backend | machine | how |
| --- | --- | --- |
| `unsloth` | G14 (CUDA) | 4-bit QLoRA, r=16, all attention + MLP projections, loss on the reply only |
| `peft` | any CUDA box | transformers + peft + trl, same recipe, slower |
| `mlx` | MacBook | `python -m mlx_lm lora --mask-prompt` on the `mlx-community/*-4bit` build |

`train.backend = "auto"` picks MLX on Apple Silicon, Unsloth if installed, else PEFT.
Defaults (`[train]` in config.toml): `Qwen/Qwen3.5-4B`, 2 epochs, lr 2e-4, batch 2 ×
grad-accum 8, seq 2048. The 9B model fits the 5070 Ti in 4-bit if you drop the batch to 1.

## Commands

```bash
soulsaka train preview                 # what the next snapshot looks like
soulsaka train run                     # snapshot + train + export as the next vN
soulsaka train run --dry-run           # snapshot only
soulsaka train list                    # versions, losses, paths
soulsaka train serve-llm --version v3  # llama-server / mlx_lm.server with the adapter
soulsaka train export v3               # regenerate the GGUF LoRA and Ollama Modelfile
```

Serving: llama.cpp applies the LoRA GGUF at load time (`--lora`), no merge needed;
Ollama users run `ollama create soulsaka-v3 -f ~/.soulsaka/adapters/v3/Modelfile`. The
`local` and `ollama` LLM profiles point at those servers; the web app's chat picks
whichever is running.

## Evals

Three signals per version, all stored in `eval_results` and shown on the Train page:

1. **Blind pairs** (`soulsaka eval pairs --version v3 --n 30`). For held-out contexts the
   model writes a reply; it is paired with the real one and shown shuffled at
   `http://<hub>:8765/rate/v3` (no pairing needed, so friends on the LAN can rate; or
   `soulsaka eval export-html`). Accuracy at 50% means indistinguishable.
2. **Discriminator** (`soulsaka eval discriminator --version v3`). A TF-IDF + logistic
   regression classifier trained real-vs-model with 5-fold CV. Its accuracy should fall
   toward 50% across versions; it is the automated proxy you can run every month without
   bothering anyone.
3. **Voice** (`soulsaka eval voice --version v3`). Speaker-embedding cosine between
   synthesised sentences and your enrolled voice, next to a real-clip baseline.

`soulsaka eval report --svg fidelity.svg` prints the table and draws the curve; the hub
serves the same chart at `/api/eval/summary.svg`.

## Where it fails

- **Not enough words.** Below ~30k words of you the adapter parrots. Import first.
- **Register contamination.** If the corpus is mostly you-talking-to-the-bot, that is
  what you get back; keep chat turns excluded and feed real conversations.
- **Parody.** Over-trained adapters exaggerate tics. The discriminator catches it: if
  accuracy goes *up* on a new version, lower epochs or learning rate.
- **VRAM.** ASR, speaker model and training do not all fit in 12 GB at once; the hub
  trains in a subprocess and you should stop `serve-llm` while a retrain runs.
