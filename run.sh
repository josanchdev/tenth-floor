#!/usr/bin/env bash
# =============================================================================
# The Tenth Floor AI — Daily Run Script
#
# Starts vLLM (if not already running), waits for the model to load,
# runs the daily pipeline, then runs the outcome checker.
#
# Usage:
#   ./run.sh                     # full universe
#   ./run.sh --dry-run           # skip DB + Discord
#   ./run.sh BTCUSDT ETHUSDT     # specific pairs
#   ./run.sh --outcomes-only     # skip pipeline, just check outcomes
#   ./run.sh --dashboard         # launch Streamlit dashboard (no vLLM)
#   ./run.sh --reset-db          # wipe and recreate DB from schema.sql
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Helpers ─────────────────────────────────────────────────────────────────
log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
die()  { log "ERROR: $*" >&2; exit 1; }

# ─── Virtual environments ────────────────────────────────────────────────────
VLLM_VENV="${SCRIPT_DIR}/.vllmenv"
APP_VENV="${SCRIPT_DIR}/.venv"

[[ -d "$VLLM_VENV" ]] || die "vLLM venv not found at ${VLLM_VENV}"
[[ -d "$APP_VENV" ]]  || die "App venv not found at ${APP_VENV}"

VLLM_BIN="${VLLM_VENV}/bin/vllm"
PYTHON_BIN="${APP_VENV}/bin/python"

[[ -x "$VLLM_BIN" ]]   || die "vllm not installed in ${VLLM_VENV} — run: ${VLLM_VENV}/bin/pip install vllm"
[[ -x "$PYTHON_BIN" ]]  || die "python not found in ${APP_VENV}"

# ─── Configuration ───────────────────────────────────────────────────────────
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_HOST="${VLLM_HOST:-localhost}"
VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen3-32B-AWQ}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-32}"  # default 256 causes OOM on 24GB GPUs
VLLM_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1"
HEALTH_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1/models"
MAX_WAIT="${MAX_WAIT:-300}"  # seconds to wait for vLLM to load the model

LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"

TODAY="$(date -u +%Y-%m-%d)"
PIPELINE_LOG="${LOG_DIR}/pipeline_${TODAY}.log"
OUTCOMES_LOG="${LOG_DIR}/outcomes_${TODAY}.log"

# ─── Load .env ───────────────────────────────────────────────────────────────
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

vllm_is_running() {
    curl -sf "${HEALTH_URL}" >/dev/null 2>&1
}

start_vllm() {
    log "Starting vLLM server (model: ${VLLM_MODEL})..."
    # Rotate old vllm log to avoid stale error confusion
    if [[ -f "${LOG_DIR}/vllm.log" ]]; then
        mv "${LOG_DIR}/vllm.log" "${LOG_DIR}/vllm_${TODAY}.log"
    fi
    nohup "$VLLM_BIN" serve "${VLLM_MODEL}" \
        --port "${VLLM_PORT}" \
        --max-model-len "${VLLM_MAX_MODEL_LEN}" \
        --gpu-memory-utilization "${VLLM_GPU_UTIL}" \
        --max-num-seqs "${VLLM_MAX_NUM_SEQS}" \
        >> "${LOG_DIR}/vllm.log" 2>&1 &

    VLLM_PID=$!
    log "vLLM started (PID ${VLLM_PID})"
}

wait_for_vllm() {
    log "Waiting for vLLM to be ready at ${HEALTH_URL}..."
    local elapsed=0
    while ! vllm_is_running; do
        if (( elapsed >= MAX_WAIT )); then
            die "vLLM did not become ready within ${MAX_WAIT}s — check ${LOG_DIR}/vllm.log"
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        # Show progress every 30 seconds
        if (( elapsed % 30 == 0 )); then
            log "  ...still waiting (${elapsed}s / ${MAX_WAIT}s)"
        fi
    done
    log "vLLM is ready (took ~${elapsed}s)"
}

# ─── Parse flags ─────────────────────────────────────────────────────────────
OUTCOMES_ONLY=false
DASHBOARD=false
PASSTHROUGH_ARGS=()

for arg in "$@"; do
    if [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
        echo "Usage: ./run.sh [OPTIONS] [PAIRS...]"
        echo ""
        echo "Options:"
        echo "  --dry-run         Run pipeline but skip DB + Discord"
        echo "  --outcomes-only   Skip pipeline, just check outcomes"
        echo "  --dashboard       Launch Streamlit admin dashboard (no vLLM)"
        echo "  --reset-db        Wipe and recreate DB from schema.sql"
        echo "  --profile PROF    Risk profile overlay (validation|production)"
        echo "  -h, --help        Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./run.sh                           # full universe"
        echo "  ./run.sh --profile validation      # use validation thresholds"
        echo "  ./run.sh BTCUSDT ETHUSDT           # specific pairs"
        echo "  ./run.sh --outcomes-only            # resolve pending signals"
        echo "  ./run.sh --dashboard                # open admin dashboard"
        exit 0
    elif [[ "$arg" == "--outcomes-only" ]]; then
        OUTCOMES_ONLY=true
    elif [[ "$arg" == "--dashboard" ]]; then
        DASHBOARD=true
    else
        PASSTHROUGH_ARGS+=("$arg")
    fi
done

# ─── Dashboard mode (no vLLM needed) ─────────────────────────────────────
if [[ "$DASHBOARD" == true ]]; then
    log "Starting Streamlit dashboard..."
    exec "$PYTHON_BIN" -m streamlit run "${SCRIPT_DIR}/src/tenth_floor/dashboard/app.py"
fi

# ─── Main ────────────────────────────────────────────────────────────────────

# ─── Reset DB mode ───────────────────────────────────────────────────────────
if [[ "${PASSTHROUGH_ARGS[0]:-}" == "--reset-db" ]]; then
    DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
    if [[ -f "$DB_PATH" ]]; then
        BACKUP_PATH="${DB_PATH}.pre-reset.${TODAY}"
        cp "$DB_PATH" "$BACKUP_PATH"
        log "Backed up DB to ${BACKUP_PATH}"
        rm "$DB_PATH"
    fi
    sqlite3 "$DB_PATH" < "${SCRIPT_DIR}/db/schema.sql"
    log "DB reset — fresh schema applied to ${DB_PATH}"
    exit 0
fi

log "═══ THE TENTH FLOOR AI — ${TODAY} ═══"

# 1. Ensure vLLM is running (only needed for the pipeline, not outcomes)
if [[ "$OUTCOMES_ONLY" == false ]]; then
    if vllm_is_running; then
        log "vLLM already running at ${VLLM_URL}"
    else
        start_vllm
        wait_for_vllm
    fi
fi

# Detect --dry-run flag for downstream steps
DRY_RUN=false
for arg in "${PASSTHROUGH_ARGS[@]}"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
done

# 2. Run daily pipeline (unless --outcomes-only)
if [[ "$OUTCOMES_ONLY" == false ]]; then
    log "Running daily pipeline..."
    if "$PYTHON_BIN" -m tenth_floor.main "${PASSTHROUGH_ARGS[@]}" 2>&1 | tee -a "$PIPELINE_LOG"; then
        log "Pipeline complete — log: ${PIPELINE_LOG}"
    else
        log "WARNING: Pipeline exited with errors — check ${PIPELINE_LOG}"
    fi
fi

# 3. Generate tweet draft (skip on --dry-run)
if [[ "$DRY_RUN" == false && "$OUTCOMES_ONLY" == false ]]; then
    log "Generating tweet draft..."
    if "$PYTHON_BIN" -m tenth_floor.post_tweet --draft-only 2>&1 | tee -a "$PIPELINE_LOG"; then
        log "Tweet draft generated"
    else
        log "WARNING: Tweet draft generation failed"
    fi
fi

# 4. Run outcome checker (skip on --dry-run)

if [[ "$DRY_RUN" == false ]]; then
    log "Running outcome checker..."
    if "$PYTHON_BIN" -m tenth_floor.check_outcomes 2>&1 | tee -a "$OUTCOMES_LOG"; then
        log "Outcome checker complete — log: ${OUTCOMES_LOG}"
    else
        log "WARNING: Outcome checker exited with errors — check ${OUTCOMES_LOG}"
    fi
else
    log "Skipping outcome checker (dry run)"
fi

# 5. DB backup
DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
if [[ -f "$DB_PATH" && "$DRY_RUN" == false ]]; then
    BACKUP_PATH="${DB_PATH}.bak"
    sqlite3 "$DB_PATH" ".backup ${BACKUP_PATH}"
    log "DB backed up to ${BACKUP_PATH}"
fi

log "═══ Done ═══"
