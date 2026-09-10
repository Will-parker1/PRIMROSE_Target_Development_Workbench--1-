#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_port="${PRIMROSE_LOCAL_BACKEND_PORT:-8090}"
backend_pid=""

export PRIMROSE_BACKEND_URL="${PRIMROSE_BACKEND_URL:-http://127.0.0.1:${backend_port}}"

cleanup() {
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

bash "$repo_root/scripts/sync-workbench.sh"

# Prefer the backend virtualenv: the optional LangChain/langextract toolchain
# lives there, and without it extraction, GraphRAG and target-development
# generation all fail their availability checks and fall back to deterministic.
backend_python="python3"
if [[ -x "$repo_root/backend/.venv/bin/python" ]]; then
  backend_python="$repo_root/backend/.venv/bin/python"
fi

if [[ "${PRIMROSE_SKIP_LOCAL_BACKEND:-0}" != "1" ]]; then
  "$backend_python" "$repo_root/backend/server.py" --no-browser --port "$backend_port" &
  backend_pid=$!
fi

cd "$repo_root"
WRANGLER_LOG_PATH=.wrangler/wrangler.log vite "$@"
