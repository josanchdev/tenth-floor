#!/usr/bin/env bash
# =============================================================================
# The Tenth Floor AI — Run Script
#
# Default mode (gaming PC / 5090): Docker — starts vLLM + pipeline, tears
# down when pipeline exits. GPU idles between runs.
#
# Local mode (dev machine — no Docker): pass --local to use .vllmenv + .venv
# directly. Same pipeline, no containers.
#
# Usage:
#   ./run.sh                           # full universe (Docker)
#   ./run.sh --local                   # full universe (local venvs, no Docker)
#   ./run.sh --dry-run                 # no DB writes, no Discord posts
#   ./run.sh --profile validation      # use validation risk thresholds
#   ./run.sh --asset-class crypto      # crypto only
#   ./run.sh BTCUSDT AAPL SPY          # specific symbols
#   ./run.sh --outcomes-only           # resolve PENDING signals (no vLLM)
#   ./run.sh --reset-db                # wipe and recreate DB from schema.sql
#   ./run.sh --dashboard               # Streamlit dashboard (local Python)
#
# Hardware profiles (Docker):
#   cp .env.3090 .env   # RTX 3090: AWQ, 10K context
#   cp .env.5090 .env   # RTX 5090: full context, higher throughput
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
die()  { log "ERROR: $*" >&2; exit 1; }

# ─── Load .env ───────────────────────────────────────────────────────────────
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

# ─── Parse args ──────────────────────────────────────────────────────────────
OUTCOMES_ONLY=false
DASHBOARD=false
RESET_DB=false
LOCAL_MODE=false
PIPELINE_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<EOF
Usage: ./run.sh [OPTIONS] [SYMBOLS...]

Options:
  --local                  Use local venvs instead of Docker (dev machine)
  --dry-run                Skip DB writes and Discord posts
  --profile validation     Use validation risk thresholds
  --profile production     Use production risk thresholds
  --asset-class CLASS      Run for one asset class (crypto|equity|etf|commodity)
  --outcomes-only          Resolve pending signals only (no vLLM, no pipeline)
  --reset-db               Wipe and recreate signal DB from schema.sql
  --dashboard              Launch Streamlit dashboard (local Python)
  -h, --help               Show this help

Examples:
  ./run.sh                           # full daily run (Docker)
  ./run.sh --local                   # full daily run (local venvs)
  ./run.sh --local --dry-run         # test on dev machine
  ./run.sh --outcomes-only           # resolve pending signals
EOF
            exit 0
            ;;
        --outcomes-only) OUTCOMES_ONLY=true ;;
        --dashboard)     DASHBOARD=true ;;
        --reset-db)      RESET_DB=true ;;
        --local)         LOCAL_MODE=true ;;
        *) PIPELINE_ARGS="${PIPELINE_ARGS} ${arg}" ;;
    esac
done

PIPELINE_ARGS="${PIPELINE_ARGS# }"  # strip leading space

# ─── Dashboard mode (local Python, no Docker) ────────────────────────────────
if [[ "$DASHBOARD" == true ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
    [[ -x "$PYTHON_BIN" ]] || die "Local .venv not found — dashboard requires local install"
    log "Starting Streamlit dashboard..."
    exec "$PYTHON_BIN" -m streamlit run "${SCRIPT_DIR}/src/tenth_floor/dashboard/app.py"
fi

# ─── Reset DB (no Docker needed — data/ is a bind mount) ─────────────────────
if [[ "$RESET_DB" == true ]]; then
    DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
    TODAY="$(date -u +%Y-%m-%d)"
    if [[ -f "$DB_PATH" ]]; then
        BACKUP_PATH="${DB_PATH}.pre-reset.${TODAY}"
        cp "$DB_PATH" "$BACKUP_PATH"
        log "Backed up DB to ${BACKUP_PATH}"
        rm "$DB_PATH"
    fi
    sqlite3 "$DB_PATH" < "${SCRIPT_DIR}/db/schema.sql"
    log "DB reset — fresh schema applied"
    exit 0
fi

# ─── Local mode (dev machine — no Docker) ────────────────────────────────────
if [[ "$LOCAL_MODE" == true ]]; then
    VLLM_VENV="${SCRIPT_DIR}/.vllmenv"
    APP_VENV="${SCRIPT_DIR}/.venv"
    VLLM_BIN="${VLLM_VENV}/bin/vllm"
    PYTHON_BIN="${APP_VENV}/bin/python"
    [[ -x "$VLLM_BIN" ]]  || die "vLLM venv not found at ${VLLM_VENV}"
    [[ -x "$PYTHON_BIN" ]] || die "App venv not found at ${APP_VENV}"

    VLLM_PORT="${VLLM_PORT:-8000}"
    VLLM_HOST="${VLLM_HOST:-localhost}"
    VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen3-32B-AWQ}"
    VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-10240}"
    VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
    VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-32}"
    HEALTH_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1/models"
    MAX_WAIT="${MAX_WAIT:-300}"
    TODAY="$(date -u +%Y-%m-%d)"
    LOG_DIR="${SCRIPT_DIR}/logs"
    mkdir -p "$LOG_DIR"

    vllm_running() { curl -sf "${HEALTH_URL}" >/dev/null 2>&1; }

    if [[ "$OUTCOMES_ONLY" == true ]]; then
        log "═══ THE TENTH FLOOR AI — outcomes check (local) ═══"
        "$PYTHON_BIN" -m tenth_floor.check_outcomes
        log "═══ Done ═══"
        exit 0
    fi

    log "═══ THE TENTH FLOOR AI — ${TODAY} (local) ═══"

    if vllm_running; then
        log "vLLM already running"
    else
        log "Starting vLLM (model: ${VLLM_MODEL})..."
        [[ -f "${LOG_DIR}/vllm.log" ]] && mv "${LOG_DIR}/vllm.log" "${LOG_DIR}/vllm_${TODAY}.log"
        nohup "$VLLM_BIN" serve "${VLLM_MODEL}" \
            --port "${VLLM_PORT}" \
            --max-model-len "${VLLM_MAX_MODEL_LEN}" \
            --gpu-memory-utilization "${VLLM_GPU_UTIL}" \
            --max-num-seqs "${VLLM_MAX_NUM_SEQS}" \
            >> "${LOG_DIR}/vllm.log" 2>&1 &
        VLLM_PID=$!
        log "vLLM started (PID ${VLLM_PID})"

        log "Waiting for vLLM to be ready..."
        elapsed=0
        while ! vllm_running; do
            (( elapsed >= MAX_WAIT )) && die "vLLM did not become ready within ${MAX_WAIT}s"
            sleep 5; elapsed=$((elapsed + 5))
            (( elapsed % 30 == 0 )) && log "  ...waiting (${elapsed}s/${MAX_WAIT}s)"
        done
        log "vLLM ready (~${elapsed}s)"
    fi

    DRY_RUN=false
    for a in $PIPELINE_ARGS; do [[ "$a" == "--dry-run" ]] && DRY_RUN=true; done

    log "Running pipeline..."
    # shellcheck disable=SC2086
    "$PYTHON_BIN" -m tenth_floor.main $PIPELINE_ARGS 2>&1 | tee -a "${LOG_DIR}/pipeline_${TODAY}.log"

    if [[ "$DRY_RUN" == false ]]; then
        log "Generating tweet draft..."
        "$PYTHON_BIN" -m tenth_floor.post_tweet --draft-only 2>&1 | tee -a "${LOG_DIR}/pipeline_${TODAY}.log" || true

        log "Running outcome checker..."
        "$PYTHON_BIN" -m tenth_floor.check_outcomes 2>&1 | tee -a "${LOG_DIR}/outcomes_${TODAY}.log" || true

        DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
        [[ -f "$DB_PATH" ]] && sqlite3 "$DB_PATH" ".backup ${DB_PATH}.bak" && log "DB backed up"
    fi

    log "═══ Done ═══"
    exit 0
fi

# ─── Outcomes-only (Docker) ───────────────────────────────────────────────────
if [[ "$OUTCOMES_ONLY" == true ]]; then
    log "═══ THE TENTH FLOOR AI — outcomes check ═══"
    log "Building pipeline image..."
    docker compose build pipeline

    log "Running outcome checker..."
    docker compose run --rm \
        -e TENTH_FLOOR_ROOT=/app \
        pipeline \
        python -m tenth_floor.check_outcomes

    log "═══ Done ═══"
    exit 0
fi

# ─── Full run: vLLM + pipeline (Docker) ──────────────────────────────────────
TODAY="$(date -u +%Y-%m-%d)"
log "═══ THE TENTH FLOOR AI — ${TODAY} ═══"
log "Pipeline args: ${PIPELINE_ARGS:-<none>}"

# Ensure data and logs directories exist before bind-mounting
mkdir -p "${SCRIPT_DIR}/data" "${SCRIPT_DIR}/logs"

# Export so docker-compose.yml substitution picks it up
export PIPELINE_ARGS

log "Starting vLLM + pipeline (this tears down automatically when pipeline exits)..."
docker compose up \
    --build \
    --abort-on-container-exit \
    --exit-code-from pipeline

log "═══ Done ═══"
