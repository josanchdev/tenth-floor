#!/usr/bin/env bash
# =============================================================================
# The Tenth Floor — dashboard dev launcher.
#
# Starts the FastAPI backend (uvicorn on 127.0.0.1:8765) and the Vite dev
# server (http://localhost:5173) in parallel. Ctrl-C stops both.
#
# vLLM is managed from the dashboard itself via the LLM status pill —
# no need to start it here.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || { echo "App venv not found at ${SCRIPT_DIR}/.venv" >&2; exit 1; }
[[ -d "${SCRIPT_DIR}/dashboard/node_modules" ]] || { echo "Run 'cd dashboard && npm install' first" >&2; exit 1; }

# Load .env so LANGFUSE + VLLM_* reach uvicorn.
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

mkdir -p "${SCRIPT_DIR}/logs"
API_LOG="${SCRIPT_DIR}/logs/api.log"

cleanup() {
    trap - INT TERM EXIT
    echo
    echo "stopping dashboard..."
    # Kill the whole process group so child uvicorn workers also die.
    [[ -n "${API_PID:-}" ]] && kill -- -$API_PID 2>/dev/null || true
    [[ -n "${VITE_PID:-}" ]] && kill -- -$VITE_PID 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Fail fast if something already holds the API port. Otherwise uvicorn exits
# with "address already in use" into the log file, the script keeps running,
# and the browser shows proxy errors that look like application bugs.
if (exec 3<>/dev/tcp/127.0.0.1/8765) 2>/dev/null; then
    exec 3<&- 3>&-
    echo "Port 8765 is already in use — another API instance is still running." >&2
    echo "Find it with:  ss -ltnp | grep 8765    then stop it before retrying." >&2
    exit 1
fi

echo "→ api:   http://127.0.0.1:8765  (logs: ${API_LOG})"
echo "→ app:   http://localhost:5173"
echo

# --reload-dir confines the watcher to source. Watching the whole project root
# also picks up .venv, node_modules, logs/ and the runtime data/ directory,
# which makes the reloader cycle the server while it is serving requests.
setsid "$PYTHON_BIN" -m uvicorn tenth_floor.api.app:app \
    --host 127.0.0.1 --port 8765 --reload \
    --reload-dir "${SCRIPT_DIR}/src" \
    >"$API_LOG" 2>&1 &
API_PID=$!

setsid bash -c 'cd dashboard && npm run dev' &
VITE_PID=$!

wait $VITE_PID $API_PID
