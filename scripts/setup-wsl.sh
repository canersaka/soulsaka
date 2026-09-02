#!/usr/bin/env bash
# Hub setup inside WSL2 (Ubuntu) on the G14: Python env, llama.cpp with CUDA, base GGUF.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
uv sync --extra hub
uv pip install -r requirements/train.txt || echo "training deps failed; fix torch/CUDA first (see docs/SETUP.md)"

LLAMA_DIR="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
if [ ! -d "$LLAMA_DIR" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi
if [ ! -x "$LLAMA_DIR/build/bin/llama-server" ]; then
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$LLAMA_DIR/build" --config Release -j"$(nproc)" --target llama-server
fi
uv pip install -r "$LLAMA_DIR/requirements/requirements-convert_lora_to_gguf.txt" 2>/dev/null || true

MODEL_DIR="${SOULSAKA_DATA_DIR:-$HOME/.soulsaka}/models"
mkdir -p "$MODEL_DIR"
GGUF="$MODEL_DIR/Qwen3.5-4B-Q4_K_M.gguf"
if [ ! -f "$GGUF" ]; then
  uv run --with huggingface_hub hf download unsloth/Qwen3.5-4B-GGUF Qwen3.5-4B-Q4_K_M.gguf --local-dir "$MODEL_DIR" \
    || uv run --with huggingface_hub huggingface-cli download unsloth/Qwen3.5-4B-GGUF Qwen3.5-4B-Q4_K_M.gguf --local-dir "$MODEL_DIR"
fi

CONFIG="${SOULSAKA_DATA_DIR:-$HOME/.soulsaka}/config.toml"
[ -f "$CONFIG" ] || uv run soulsaka init >/dev/null
python3 - "$CONFIG" "$LLAMA_DIR" "$GGUF" <<'PY'
import re, sys, pathlib
path, llama, gguf = sys.argv[1:]
p = pathlib.Path(path); s = p.read_text()
def setkey(s, key, val):
    pat = re.compile(rf'^{key} = .*$', re.M)
    line = f'{key} = "{val}"'
    return pat.sub(line, s) if pat.search(s) else s.replace("[train]\n", f"[train]\n{line}\n")
s = setkey(s, "llama_cpp_dir", llama)
s = setkey(s, "base_gguf", gguf)
p.write_text(s)
PY
echo "done. next: uv run soulsaka init --name ... (if you skipped it), then uv run soulsaka serve"
