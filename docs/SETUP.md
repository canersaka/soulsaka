# Setup

Three machines, three roles. The **hub** does all the model work and owns the data.
Everything else is a client that pairs with it once.

| Machine | Role | Install |
| --- | --- | --- |
| ROG G14 (Ryzen AI 9 HX 370, RTX 5070 Ti, Windows) | hub (CUDA), optional listener | WSL2 for the hub, native Windows for the mic |
| MacBook Pro (M1 Pro) | importer + listener, or hub (Metal/MLX) | Homebrew + uv |
| iPhone | capture + chat | Safari, add to Home Screen |

All commands assume the repo is cloned and [uv](https://docs.astral.sh/uv/) is installed
(`curl -LsSf https://astral.sh/uv/install.sh | sh`, or `winget install astral-sh.uv`).

## 1. Hub on the G14 (Windows + WSL2)

WSL2 is the path of least resistance for Unsloth, faster-whisper and llama.cpp with CUDA.
The Windows NVIDIA driver is all you need on the Windows side; do not install a Linux
driver inside WSL.

```bash
# inside Ubuntu (WSL2)
sudo apt update && sudo apt install -y build-essential cmake git ffmpeg
git clone https://github.com/canersaka/pkmntv.git soulsaka && cd soulsaka
scripts/setup-wsl.sh          # uv sync --extra hub, llama.cpp with CUDA, base GGUF
soulsaka init --name "Caner" --email you@example.com --phone "+1 617 555 0199"
soulsaka serve                  # prints URLs + a pairing code
```

`scripts/setup-wsl.sh` also writes `train.base_gguf` and `train.llama_cpp_dir` into
`~/.soulsaka/config.toml`. Then:

- **Reach the hub from the LAN.** WSL2 sits behind NAT by default. Either enable
  mirrored networking (add `networkingMode=mirrored` under `[wsl2]` in
  `%UserProfile%\.wslconfig`, then `wsl --shutdown`) or forward the port from Windows:
  `netsh interface portproxy add v4tov4 listenport=8765 listenaddress=0.0.0.0 connectport=8765 connectaddress=$(wsl hostname -I)`.
  Allow 8765 through Windows Defender Firewall either way.
- **Training deps**: `uv pip install -r requirements/train.txt` (Unsloth, TRL, PEFT,
  bitsandbytes). If torch does not see the GPU, install the CUDA 12.8 wheel from
  pytorch.org first; the 5070 Ti (Blackwell) needs a recent build.
- **Serve the adapter**: after `soulsaka train run`, `soulsaka train serve-llm` starts
  `llama-server` on :8080 with the base GGUF plus the LoRA; the `local` LLM profile
  already points there. Pass `--chat-template-kwargs '{"enable_thinking": false}'`
  via `train.serve_extra_args` if the base model has a thinking mode you want off.
- **Run it as a service**: `scripts/soulsaka-hub.service` is a systemd unit for WSL2
  (`systemd=true` in `/etc/wsl.conf`).

The microphone is not visible inside WSL2, so the always-on listener runs in native
Windows Python: `scripts/setup-windows.ps1` creates a venv with `soulsaka[listener]`, then
`soulsaka hub login --url http://localhost:8765 --code XXXX` and `soulsaka listen`.

## 2. MacBook

### As importer and listener (the usual case)

```bash
brew install uv ffmpeg
git clone https://github.com/canersaka/pkmntv.git soulsaka && cd soulsaka
uv sync --extra listener
uv run soulsaka hub login --url http://<g14-ip>:8765 --code XXXXXXXX
uv run soulsaka import auto      # iMessage, WhatsApp Desktop, Apple Mail, git
uv run soulsaka listen           # always-on mic
```

`import auto` needs to read `~/Library/Messages/chat.db`. Give your terminal **Full Disk
Access** (System Settings → Privacy & Security → Full Disk Access) or it will tell you
so. WhatsApp Desktop's `ChatStorage.sqlite` and Apple Mail's `.emlx` files are found the
same way. For Gmail, `soulsaka import imap --host imap.gmail.com --user you@gmail.com`
asks for an app password. Drop WhatsApp `.txt`/`.zip` exports or a Discord data package
on the Corpus page, or pass them to `soulsaka import whatsapp-export` / `soulsaka import discord`.

Run the listener at login with `scripts/com.soulsaka.listener.plist` (copy to
`~/Library/LaunchAgents/`, edit the paths, `launchctl load`).

### As the hub (Metal / MLX)

```bash
scripts/setup-mac.sh hub       # uv sync --extra hub, mlx-lm, mlx-whisper, llama.cpp
soulsaka init --name "Caner" ...
soulsaka serve
```

Config differences that the script applies: `asr.backend = "mlx-whisper"`,
`train.backend = "mlx"`, and `train serve-llm` launches `mlx_lm.server` with the
adapter. Speaker verification runs on CPU (fast enough). A 4B model in 4-bit trains
comfortably in 16 GB.

## 3. iPhone

1. On the same Wi-Fi, open the hub URL that `soulsaka serve` printed (e.g.
   `http://192.168.1.20:8765`) in Safari.
2. Enter the pairing code once (or open `http://<hub>:8765/?pair=CODE`).
3. Share → **Add to Home Screen**. The app now works offline: notes and voice clips queue
   in the phone and upload when the hub is reachable again.
4. Allow the microphone when asked. Push-to-talk works everywhere; "always listening"
   works while the app is in the foreground. iOS does not allow a web app to keep the
   mic open in the background, so the always-on mic lives on the Mac or the G14.

## 4. Away from home

Install [Tailscale](https://tailscale.com) on the hub and the phone; use the hub's
tailnet address (`http://g14:8765` with MagicDNS) as the hub URL. Nothing else changes;
the hub still never talks to the internet.

## 5. Cloud models (optional)

Set `privacy.allow_cloud_llm = true` in `config.toml`, then export `ANTHROPIC_API_KEY`
or `OPENAI_API_KEY` before `soulsaka serve`. The `claude` and `openai` profiles become
selectable in Chat, labelled as cloud. See PRIVACY.md for what is sent and the note on
consumer subscriptions.

## 6. The monthly loop

`scripts/retrain.sh` does one full cycle: dataset snapshot → QLoRA retrain from base →
GGUF export → self-model → blind pairs → discriminator → report. Put it in cron
(WSL2: `crontab -e`, `0 3 1 * * /path/to/soulsaka/scripts/retrain.sh`) or run it by hand.
