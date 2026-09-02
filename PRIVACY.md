# Privacy

The promise is that nothing leaves my machines. This file says exactly what is stored and where, and what the few exceptions are.

## Where the data is

Everything lives in one folder, `~/.soulsaka`. Delete the folder and it has forgotten me. Inside it: `soulsaka.db` is the database with the corpus, memories, captures, devices, jobs and training records. `audio/` holds my own recordings as 16 kHz WAV files. `spool/` is the buffer a client uses while the hub is unreachable. `models/`, `adapters/`, `datasets/` and `evals/` hold downloaded models, my trained adapters, training snapshots and evaluation results. `config.toml` is the settings file and `client.json` holds a device token for a remote hub.

## Other people

Phone numbers, emails and usernames of other people are stored only as salted hashes. Their display names are kept so conversations are readable, and that can be turned off.

Other people's messages are stored as context only. The training set never uses a message that is not mine as a target.

Speech that is not my voice is discarded by default. The audio is deleted and no transcript is kept. The always-on microphone only ever adds my own words to the corpus. This is what makes an always-on mic reasonable in a place where recording other people needs their consent.

## Network

The hub listens on my home network and every device needs a token that comes from a short-lived pairing code. Tokens are stored hashed.

Requests from the hub machine itself are trusted without a token only if they carry a special header. That header forces the browser to do a preflight check, so a random website I visit cannot talk to the hub through my browser.

The hub only makes outbound connections to the model endpoints in its config. Any endpoint that is not on my own network has to be marked as cloud, and cloud endpoints are refused unless I set `allow_cloud_llm = true`. Training never uses a cloud model. Only chat prompts, with the memories retrieved for them, would ever be sent, and the app labels those replies.

Model downloads are the one other thing that goes out. Whisper, the speaker model, the embedding model and the base language model are downloaded from Hugging Face on first use.

If I want to reach the hub from outside the house I use Tailscale. It does not open any ports.

## Subscriptions

Neither Anthropic nor OpenAI offers a supported way for an app like this to use a consumer subscription. The supported way is an API key. There are also experimental profiles that run the official `claude` and `codex` command-line tools signed in with my own account. They are slow, rate-limited and subject to those tools' terms.
