# Privacy

The promise is "nothing leaves your machine", and the design tries to make that
verifiable rather than just stated.

## Where data lives

Everything is under one directory (`~/.soulsaka`, or `SOULSAKA_DATA_DIR`):

| Path | Contents |
| --- | --- |
| `soulsaka.db` | corpus, memories, captures, devices, jobs, training and eval records |
| `audio/` | your own utterances as 16 kHz WAV (only if `privacy.keep_audio`) |
| `spool/` | client-side buffer while the hub is unreachable |
| `models/`, `adapters/`, `datasets/`, `evals/` | downloaded models, your adapters, training snapshots |
| `config.toml`, `client.json` | settings; device token for a remote hub (mode 0600) |

Delete the directory and soulsaka has forgotten you.

## Other people

- Phone numbers, emails and usernames of other people are stored **only as salted
  SHA-256 hashes**. Display names are kept for readability (`privacy.keep_contact_names`).
- Other people's messages are stored as **context only**. The dataset builder never uses a
  message that is not yours as a training target.
- Speech that is not your voice is **discarded** by default (`privacy.other_speakers =
  "discard"`): the audio is deleted and no transcript is stored. The listener only ever
  contributes your own utterances to the corpus. Massachusetts, among other places, is a
  two-party consent state for recording; this default is what makes an always-on
  microphone reasonable there.

## Network

- The hub binds to your LAN and requires a per-device bearer token minted from a
  short-lived pairing code. Tokens are stored hashed.
- Requests from the hub machine itself are trusted only when they carry the
  `X-Soulsaka-Client` header. That header forces a CORS preflight, so a web page you happen
  to visit cannot talk to the hub through your browser.
- The hub makes outbound connections only to configured LLM profiles. A profile whose
  address is not local must be marked `cloud = true`, and cloud profiles are refused
  unless `privacy.allow_cloud_llm = true`. Training never uses a cloud model; only chat
  prompts (with retrieved memories) would be sent, and the UI labels those replies.
- Model downloads (Whisper, ECAPA, embeddings, base LLM) are the one other kind of
  egress. They happen on first use and go to the Hugging Face hub.
- Reaching the hub from outside your home network is your call: Tailscale keeps it
  private without opening ports.

## Cloud subscriptions

Neither Anthropic nor OpenAI offers a supported way for a third-party app to use a
consumer Claude Pro/Max or ChatGPT Plus subscription. The supported route is an API key
(pay as you go), configured as the `claude` / `openai` profiles. The `claude-cli` and
`codex-cli` profiles shell out to the official command-line tools signed in with your own
account; they are experimental, rate-limited, and subject to those tools' terms.
