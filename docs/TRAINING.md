# Training and evaluation

## The loop

The corpus grows every day. `soulsaka train run` takes a snapshot of everything so far, trains an adapter from the base model, saves it as the next version, and exports it for serving. `soulsaka train serve-llm` serves it. Then the evals run against held-out conversations and the report shows the curve over versions.

Every version is a full retrain from the base model on everything up to its cutoff date, never a continuation of the previous adapter. Incremental training drifts and forgets. Full retrains are reproducible (the snapshot's hash is recorded) and comparable, which is what makes the curve mean something.

## What a training example looks like

Each example is a chat. The system prompt says who I am, what register this is (text, email, speech or prose), which language, and the setting, like "1:1 whatsapp conversation with Ali". Then come a few prior turns as context, and the last turn is my reply. Only my reply is the target.

The rules, all in `src/soulsaka/train/dataset.py`:

- Only my messages are targets. Other people's messages only appear as context. In group chats each context line is prefixed with the sender's name.
- Messages from one side within 20 minutes are merged into one turn, so a burst like "yeah" and "probably around 8" is learned as one reply.
- Up to 8 prior turns, none older than three days, trimmed to fit the sequence length.
- Chat with the assistant itself is left out by default, because talking to a bot is a narrow way of writing.
- Things without a partner, like commit messages and documents, get a short instruction as the user turn.
- Media placeholders, link-only and one-word replies are dropped. Duplicates are dropped. Each conversation contributes at most 3000 examples.
- 5 percent of conversations are held out, chosen by a hash so the split is the same in every version. Evals only ever use held-out conversations.

`soulsaka train preview` prints the counts and a few examples before spending any GPU time.

## Backends

`unsloth` is for a CUDA GPU: 4-bit base model, LoRA rank 16 on the attention and MLP layers, and the loss only on the reply. `peft` is the same recipe without Unsloth for machines that cannot install it. `mlx` is for a Mac, using mlx-lm on the 4-bit community build of the same model. `train.backend = "auto"` picks MLX on Apple Silicon, Unsloth if it is installed, otherwise PEFT.

The defaults are Qwen3.5-4B, 2 epochs, learning rate 2e-4, batch 2 with gradient accumulation 8, sequence length 2048. The 9B model fits a 12 GB GPU in 4-bit if the batch size is dropped to 1.

## Commands

```bash
soulsaka train preview
soulsaka train run
soulsaka train run --dry-run
soulsaka train list
soulsaka train serve-llm --version v3
soulsaka train export v3
```

llama.cpp applies the small LoRA file on top of the base model at load time, so nothing gets merged or re-quantised. For Ollama there is a Modelfile next to each adapter.

## Evals

Three numbers per version:

Blind pairs. `soulsaka eval pairs --version v3 --n 30` takes held-out contexts, asks the model for a reply, and pairs it with my real reply in random order. Friends open `http://<hub>/rate/v3` (no pairing needed) and guess. 50 percent accuracy means they cannot tell.

Discriminator. `soulsaka eval discriminator --version v3` trains a classifier to tell my replies from the model's and reports cross-validated accuracy. It should fall toward 50 percent over versions. It is the automated stand-in for the blind test that can run every month without asking anyone.

Voice. `soulsaka eval voice --version v3` synthesises a few of my sentences and compares the speaker embedding to my enrolled voice, next to a baseline from real clips.

`soulsaka eval report` prints the table and `--svg` draws the chart. The Train page in the app shows the same thing.

## Where it goes wrong

Not enough words: under about 30k words of me the adapter just parrots. Import first.

Wrong kind of words: if the corpus is mostly me talking to the bot, that is what comes back. Keep chat turns excluded and feed real conversations.

Over-training: an over-trained adapter exaggerates my tics. The discriminator catches it. If its accuracy goes up on a new version, lower the epochs or the learning rate.

Memory: speech recognition, the speaker model and training do not all fit in a small GPU at once. The hub trains in a separate process, and the model server should be stopped during a retrain.
