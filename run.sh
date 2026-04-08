#!/usr/bin/env bash
# =============================================================================
# The Tenth Floor AI — Run Script
#
# Starts vLLM + pipeline in Docker, waits for vLLM to load the model, runs
# the pipeline once, tears everything down. GPU idles between runs.
#
# First run: Docker pulls vllm/vllm-openai and downloads model weights (~20 GB).
# Subsequent runs: weights are cached in the huggingface_cache volume — starts
# in ~2 minutes.
#
# Usage:
#   ./run.sh                           # full universe, all asset classes
#   ./run.sh --dry-run                 # no DB writes, no Discord posts
#   ./run.sh --profile validation      # use validation risk thresholds
#   ./run.sh --asset-class crypto      # crypto only
#   ./run.sh BTCUSDT AAPL SPY          # specific symbols
#   ./run.sh --outcomes-only           # resolve PENDING signals (no vLLM)
#   ./run.sh --reset-db                # wipe and recreate DB from schema.sql
#   ./run.sh --dashboard               # Streamlit dashboard (local Python, no Docker)
#
# Hardware profiles:
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
PIPELINE_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<EOF
Usage: ./run.sh [OPTIONS] [SYMBOLS...]

Options:
  --dry-run                Skip DB writes and Discord posts
  --profile validation     Use validation risk thresholds
  --profile production     Use production risk thresholds
  --asset-class CLASS      Run for one asset class (crypto|equity|etf|commodity)
  --outcomes-only          Resolve pending signals only (no vLLM, no pipeline)
  --reset-db               Wipe and recreate signal DB from schema.sql
  --dashboard              Launch Streamlit dashboard (local Python, no Docker)
  -h, --help               Show this help

Examples:
  ./run.sh                           # full daily run
  ./run.sh --dry-run                 # test without side effects
  ./run.sh BTCUSDT AAPL              # specific symbols
  ./run.sh --outcomes-only           # resolve pending signals
EOF
            exit 0
            ;;
        --outcomes-only) OUTCOMES_ONLY=true ;;
        --dashboard)     DASHBOARD=true ;;
        --reset-db)      RESET_DB=true ;;
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

# ─── Outcomes-only (pipeline container, no vLLM) ─────────────────────────────
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

# ─── Full run: vLLM + pipeline ────────────────────────────────────────────────
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
