#!/usr/bin/env bash
# =============================================================================
# The Tenth Floor — CLI helper (dev / ops only)
#
# The operational path is the dashboard Run button (see ./dashboard.sh).
# This script covers the three things that still need a shell entry point:
#
#   ./run.sh --local                 # headless local pipeline run (no Docker)
#   ./run.sh --outcomes-only         # resolve OPEN signals
#   ./run.sh --reset-db              # wipe + recreate the signal DB from schema.sql
#
# Pipeline args (pairs, --dry-run, --profile, --asset-class) are forwarded
# verbatim to `python -m tenth_floor.main`.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Load .env so VLLM_* and LANGFUSE_* reach the child processes.
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

OUTCOMES_ONLY=false
RESET_DB=false
LOCAL_MODE=false
PIPELINE_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --outcomes-only) OUTCOMES_ONLY=true ;;
        --reset-db)      RESET_DB=true ;;
        --local)         LOCAL_MODE=true ;;
        *)               PIPELINE_ARGS="${PIPELINE_ARGS} ${arg}" ;;
    esac
done

PIPELINE_ARGS="${PIPELINE_ARGS# }"

# ─── Reset DB ───────────────────────────────────────────────────────────────
if [[ "$RESET_DB" == true ]]; then
    DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
    TODAY="$(date -u +%Y-%m-%d)"
    if [[ -f "$DB_PATH" ]]; then
        BACKUP_PATH="${DB_PATH}.pre-reset.${TODAY}"
        cp "$DB_PATH" "$BACKUP_PATH"
        log "Backed up DB to ${BACKUP_PATH}"
        rm "$DB_PATH"
    fi
    mkdir -p "$(dirname "$DB_PATH")"
    sqlite3 "$DB_PATH" < "${SCRIPT_DIR}/db/schema.sql"
    log "DB reset — fresh schema applied"
    exit 0
fi

# Everything below needs --local.
[[ "$LOCAL_MODE" == true ]] || die "pass --local (Docker pipeline runs via the dashboard Run button)"

APP_VENV="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${APP_VENV}/bin/python"
[[ -x "$PYTHON_BIN" ]] || die "App venv not found at ${APP_VENV}"

# ─── Outcomes-only ──────────────────────────────────────────────────────────
if [[ "$OUTCOMES_ONLY" == true ]]; then
    log "═══ Outcome check (local) ═══"
    "$PYTHON_BIN" -m tenth_floor.check_outcomes
    log "═══ Done ═══"
    exit 0
fi

# ─── Headless local pipeline run ────────────────────────────────────────────
# Assumes vLLM is already serving on VLLM_PORT. Start it from the dashboard
# LLM pill, or with: nohup .vllmenv/bin/vllm serve ... >> logs/vllm.log &
VLLM_HOST="${VLLM_HOST:-localhost}"
VLLM_PORT="${VLLM_PORT:-8000}"
HEALTH_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1/models"
curl -sf "${HEALTH_URL}" >/dev/null 2>&1 || die "vLLM not reachable at ${HEALTH_URL} — start it first"

TODAY="$(date -u +%Y-%m-%d)"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"

log "═══ Local pipeline run — ${TODAY} ═══"
# shellcheck disable=SC2086
"$PYTHON_BIN" -m tenth_floor.main $PIPELINE_ARGS 2>&1 | tee -a "${LOG_DIR}/pipeline_${TODAY}.log"

DRY_RUN=false
for a in $PIPELINE_ARGS; do [[ "$a" == "--dry-run" ]] && DRY_RUN=true; done
if [[ "$DRY_RUN" == false ]]; then
    log "Running outcome checker..."
    "$PYTHON_BIN" -m tenth_floor.check_outcomes 2>&1 | tee -a "${LOG_DIR}/outcomes_${TODAY}.log" || true
    DB_PATH="${SCRIPT_DIR}/data/playbook_history.db"
    [[ -f "$DB_PATH" ]] && sqlite3 "$DB_PATH" ".backup ${DB_PATH}.bak" && log "DB backed up"
fi

log "═══ Done ═══"
