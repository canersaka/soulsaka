# Setup

One machine is the hub. It does all the model work and owns the data. Everything else pairs with it once and just sends captures.

Every command below assumes the repo is cloned and `uv` is installed.

## The Windows machine as the hub

I run the hub inside WSL2 because Unsloth, faster-whisper and llama.cpp with CUDA all work best on Linux. The normal Windows NVIDIA driver is enough, WSL2 uses it directly.

```bash
sudo apt update && sudo apt install -y build-essential cmake git ffmpeg
git clone https://github.com/canersaka/soulsaka.git && cd soulsaka
scripts/setup-wsl.sh
soulsaka init --name "Your Name" --email you@example.com --phone "+1 555 000 0000"
soulsaka serve
```

The script installs the Python environment, builds llama.cpp with CUDA, downloads the base model and writes the paths into `~/.soulsaka/config.toml`.

WSL2 sits behind its own network by default, so other devices cannot see the hub until either mirrored networking is turned on (`networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`) or the port is forwarded from Windows with `netsh interface portproxy`. Either way port 8765 has to be allowed through the Windows firewall.

Training needs one more install: `uv pip install -r requirements/train.txt`. If torch does not see the GPU afterwards, install the CUDA build from pytorch.org first.

After a training run, `soulsaka train serve-llm` starts llama-server with the base model and the new adapter. The default chat profile already points at it.

The microphone is not visible from inside WSL2. For the always-on mic on Windows, run `scripts/setup-windows.ps1` in normal PowerShell, then `soulsaka hub login --url http://localhost:8765 --code XXXX` and `soulsaka listen`.

To keep the hub running in the background there is a systemd unit in `scripts/soulsaka-hub.service`.

## The Mac

Usually the Mac is where the messages are, so it is the importer and the always-on mic:

```bash
brew install uv ffmpeg
git clone https://github.com/canersaka/soulsaka.git && cd soulsaka
uv sync --extra listener
uv run soulsaka hub login --url http://<hub-address>:8765 --code XXXXXXXX
uv run soulsaka import --auto
uv run soulsaka listen
```

`import --auto` needs to read the Messages database, so the terminal needs Full Disk Access (System Settings, Privacy & Security, Full Disk Access). It tells you if it cannot read something. It finds iMessage, WhatsApp Desktop and Apple Mail on its own. For Gmail, `soulsaka import imap --host imap.gmail.com --user you@gmail.com` asks for an app password. WhatsApp exports and Discord data packages can be dropped on the Corpus page in the app or passed to `soulsaka import whatsapp-export` and `soulsaka import discord`.

To run the mic at login, copy `scripts/com.soulsaka.listener.plist` to `~/Library/LaunchAgents/`, edit the paths, and load it.

The Mac can also be the hub instead of the Windows machine. `scripts/setup-mac.sh hub` installs everything including mlx-lm and mlx-whisper, and switches the config to the Apple backends. Training then uses MLX and `train serve-llm` starts the MLX server. A 4B model in 4-bit trains fine in 16 GB.

## The phone

On the same Wi-Fi, open the hub address in the browser, enter the pairing code once (or open `http://<hub-address>:8765/?pair=CODE`), and add it to the home screen. It works offline after that: notes and voice clips queue on the phone and upload when the hub is reachable again.

Push-to-talk works everywhere. Always-listening works while the app is open. iOS does not let a web app keep the mic on in the background, so the always-on mic lives on a laptop.

## Away from home

Install Tailscale on the hub and the phone and use the hub's Tailscale address as the hub URL. Nothing else changes and the hub still never talks to the internet.

## Cloud models

Set `allow_cloud_llm = true` under `[privacy]` in `config.toml`, export `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before starting the hub, and the cloud profiles become selectable in Chat. See `PRIVACY.md` for what gets sent.

## The monthly loop

`scripts/retrain.sh` does one full cycle: dataset snapshot, retrain from the base model, export, self-model, blind pairs, discriminator, report. Put it in cron or run it by hand.
