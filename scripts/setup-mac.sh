#!/usr/bin/env bash
# MacBook setup. `setup-mac.sh client` = importer + listener (default); `setup-mac.sh hub` = full hub with MLX.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-client}"

command -v brew >/dev/null || { echo "install Homebrew first: https://brew.sh"; exit 1; }
brew list uv >/dev/null 2>&1 || brew install uv
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

if [ "$MODE" = "hub" ]; then
  brew list llama.cpp >/dev/null 2>&1 || brew install llama.cpp
  uv sync --extra hub --extra listener
  uv pip install -r requirements/train-mlx.txt
  CONFIG="${SOULSAKA_DATA_DIR:-$HOME/.soulsaka}/config.toml"
  [ -f "$CONFIG" ] || uv run soulsaka init >/dev/null
  python3 - "$CONFIG" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = re.sub(r'^backend = "faster-whisper"$', 'backend = "mlx-whisper"', s, flags=re.M)
s = re.sub(r'^(\[train\]\n(?:.*\n)*?)backend = "auto"$', r'\1backend = "mlx"', s, flags=re.M)
p.write_text(s)
PY
  echo "hub ready: uv run soulsaka serve"
else
  uv sync --extra listener
  echo "client ready: uv run soulsaka hub login --url http://<hub>:8765 --code XXXX && uv run soulsaka import auto"
  echo "grant Full Disk Access to your terminal for iMessage/WhatsApp/Mail."
fi
