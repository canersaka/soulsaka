#!/usr/bin/env bash
# One full cycle: snapshot -> retrain from base -> export -> self-model -> evals -> report.
# Assumes the adapter server for the new version is started afterwards by hand (or by
# `soulsaka train serve-llm`) before generating blind pairs; pass --serve to do it here.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVE=0; [ "${1:-}" = "--serve" ] && SERVE=1

VERSION=$(uv run python -c "from soulsaka.hub.state import HubState; from soulsaka.config import get_settings; from soulsaka.train import registry; s=HubState(get_settings()); print(registry.next_version(s.db)); s.close()")
echo "== training $VERSION"
uv run soulsaka train run --version "$VERSION"
uv run soulsaka self-model --regenerate >/dev/null || true

if [ "$SERVE" = 1 ]; then
  echo "== serving $VERSION for evals"
  uv run soulsaka train serve-llm --version "$VERSION" & SERVER=$!
  trap 'kill $SERVER 2>/dev/null || true' EXIT
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8080/v1/models >/dev/null && break; sleep 2; done
fi
echo "== evals"
uv run soulsaka eval pairs --version "$VERSION" --n 30 || echo "pairs skipped (is the adapter server running?)"
uv run soulsaka eval discriminator --version "$VERSION" || true
uv run soulsaka eval voice --version "$VERSION" || true
uv run soulsaka eval report --svg "${SOULSAKA_DATA_DIR:-$HOME/.soulsaka}/evals/fidelity.svg"
