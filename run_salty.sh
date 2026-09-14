#!/usr/bin/env bash
# Start the SALTY data API (8010), voice call agent (8001) and web console (3000).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
API_PORT="${SALTY_API_PORT:-8010}"
AGENT_PORT="${CALL_AGENT_PORT:-8001}"

[ -x "$PYTHON" ] || { echo "No virtualenv at $ROOT/.venv - create it first." >&2; exit 1; }

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "Data API      http://127.0.0.1:$API_PORT"
(cd "$ROOT/backend" && SALTY_API_PORT="$API_PORT" "$PYTHON" api_server.py) &
pids+=($!)

echo "Call agent    http://127.0.0.1:$AGENT_PORT"
(cd "$ROOT/call-agent" && "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$AGENT_PORT") &
pids+=($!)

if [ -d "$ROOT/ui2/node_modules" ]; then
  echo "Web console   http://127.0.0.1:3000"
  (cd "$ROOT/ui2" && npm run dev) &
  pids+=($!)
fi

wait -n
