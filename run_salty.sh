#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_PORT="${SALTY_API_PORT:-8010}"
UI_PORT="${SALTY_UI_PORT:-3000}"
CALL_AGENT_PORT="${SALTY_CALL_AGENT_PORT:-8001}"
CALL_AGENT_HOST="${SALTY_CALL_AGENT_HOST:-0.0.0.0}"
API_URL="http://127.0.0.1:${API_PORT}"
CALL_AGENT_URL="http://127.0.0.1:${CALL_AGENT_PORT}"
GROQ_MODEL="${GROQ_MODEL:-openai/gpt-oss-20b}"
GROQ_BASE_URL="${GROQ_BASE_URL:-https://api.groq.com/openai/v1}"
SALTY_LIVE="${SALTY_LIVE:-1}"
# Chat is intentionally in demo mode while the marine agent is being tested.
# Change this to "live" only when the real data/tool path is ready.
SALTY_AI_MODE="${SALTY_AI_MODE:-mock}"
export GROQ_MODEL
export GROQ_BASE_URL
export SALTY_LIVE
export SALTY_AI_MODE
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

if [[ ! -d "${ROOT_DIR}/ui2/node_modules" ]]; then
  echo "UI dependencies are missing. Run: cd ui2 && npm install" >&2
  exit 1
fi

# Prevent a previous SALTY development session from winning the readiness
# check while the newly started processes fail to bind their ports.
stop_port_processes() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Stopping existing process on port ${port}..."
    kill ${pids} 2>/dev/null || true
    sleep 1
  fi
}

stop_port_processes "${API_PORT}"
stop_port_processes "${UI_PORT}"
stop_port_processes "${CALL_AGENT_PORT}"

cleanup() {
  trap - EXIT INT TERM
  [[ -n "${API_PID:-}" ]] && kill "${API_PID}" 2>/dev/null || true
  [[ -n "${UI_PID:-}" ]] && kill "${UI_PID}" 2>/dev/null || true
  [[ -n "${CALL_AGENT_PID:-}" ]] && kill "${CALL_AGENT_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Configuring Groq AI for SALTY AI (${GROQ_MODEL})..."

echo "Starting SALTY data API on ${API_URL}..."
(
  cd "${ROOT_DIR}"
  SALTY_API_PORT="${API_PORT}" "${PYTHON_BIN}" api_server.py
) &
API_PID=$!

for attempt in {1..30}; do
  if curl -fsS "${API_URL}/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${API_PID}" 2>/dev/null; then
    echo "SALTY API stopped unexpectedly." >&2
    exit 1
  fi
  sleep 1
done

if ! curl -fsS "${API_URL}/api/health" >/dev/null 2>&1; then
  echo "SALTY API did not become ready on ${API_URL}." >&2
  exit 1
fi

CALL_AGENT_ENABLED=0
CALL_AGENT_DIR="${ROOT_DIR}/SaltyAI-CallAgent"
if [[ -f "${CALL_AGENT_DIR}/app/main.py" ]]; then
  echo "Checking SALTY Call Agent dependencies..."
  if ! "${PYTHON_BIN}" -c 'import fastapi, httpx, pydantic_settings, uvicorn' >/dev/null 2>&1; then
    echo "Installing SALTY Call Agent dependencies..."
    if ! "${PYTHON_BIN}" -m pip install -r "${CALL_AGENT_DIR}/requirements.txt"; then
      echo "Could not install Call Agent dependencies; the Call Agent was not started." >&2
    fi
  fi

  if "${PYTHON_BIN}" -c 'import fastapi, httpx, pydantic_settings, uvicorn' >/dev/null 2>&1; then
    echo "Starting SALTY Call Agent on ${CALL_AGENT_URL}..."
    (
      cd "${CALL_AGENT_DIR}"
      AI_BACKEND_URL="${API_URL}" \
      CALL_AGENT_TEST_MODE=false \
      PORT="${CALL_AGENT_PORT}" \
      "${PYTHON_BIN}" -m uvicorn app.main:app --host "${CALL_AGENT_HOST}" --port "${CALL_AGENT_PORT}"
    ) &
    CALL_AGENT_PID=$!

    for attempt in {1..30}; do
      if curl -fsS "${CALL_AGENT_URL}/health/live" >/dev/null 2>&1; then
        CALL_AGENT_ENABLED=1
        break
      fi
      if ! kill -0 "${CALL_AGENT_PID}" 2>/dev/null; then
        echo "SALTY Call Agent stopped unexpectedly; voice calls will be unavailable." >&2
        break
      fi
      sleep 1
    done
    if [[ "${CALL_AGENT_ENABLED}" != "1" ]]; then
      echo "SALTY Call Agent did not become ready on ${CALL_AGENT_URL}." >&2
    fi
  fi
else
  echo "SALTY Call Agent folder not found; voice calls will be unavailable." >&2
fi

echo "Starting SALTY Marine UI on http://127.0.0.1:${UI_PORT}..."
(
  cd "${ROOT_DIR}/ui2"
  NEXT_PUBLIC_SALTY_API_URL="${API_URL}" \
  NEXT_PUBLIC_SALTY_CALL_AGENT_URL="${CALL_AGENT_URL}" \
  npm run dev -- --hostname 127.0.0.1 --port "${UI_PORT}"
) &
UI_PID=$!

echo
echo "SALTY Marine is running"
echo "  UI:  http://127.0.0.1:${UI_PORT}"
echo "  API: ${API_URL}/api/health"
echo "  Call Agent: ${CALL_AGENT_URL}/health/live"
echo "  AI:  Groq (${GROQ_MODEL}) via ${GROQ_BASE_URL}"
echo "  AI mode: ${SALTY_AI_MODE}"
if [[ "${SALTY_LIVE}" == "1" ]]; then
  echo "  Mode: live ERDDAP"
else
  echo "  Mode: prototype fallback"
fi
echo "Press Ctrl-C to stop both services."
echo

wait "${UI_PID}"
