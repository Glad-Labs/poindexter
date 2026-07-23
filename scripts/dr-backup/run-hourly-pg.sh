#!/usr/bin/env bash
# Glad Labs hourly Postgres-only snapshot.
#
# Sibling to run-backup.sh — that one runs daily at 03:00 with a full
# tree (~/.poindexter, ~/glad-labs-website, ~/glad-labs-prompts, plus
# the pg_dump). This one runs every hour, dumps poindexter_brain, and
# pushes a tiny restic snapshot tagged `pg-hourly` so a Docker reinstall
# / volume wipe (like the 2026-05-05 incident) loses at most ~1h of DB
# state instead of 12-24h.
#
# Retention: keep last 24 hourly snapshots (~24h rolling window) +
# whatever the daily prune keeps from the dr-daily tag. The hourly tag
# is independent so daily prune never touches these.
#
# Failure path: same Telegram alert pattern as run-backup.sh.

set -euo pipefail

# Every path resolves from $HOME or an env override, so this is not tied to one
# operator account or one drive letter. Defaults reproduce the Windows/Git-Bash
# layout this was written for; a Linux port sets DR_RESTIC_BIN=restic and points
# DR_BACKUP_REPO/DR_BACKUP_MOUNT at the USB mountpoint (prefer a UUID-keyed
# fstab entry over /media/$USER/... so a timer does not depend on a desktop
# automount).
RESTIC_BIN="${DR_RESTIC_BIN:-$HOME/bin/restic.exe}"
REPO="${DR_BACKUP_REPO:-F:/poindexter-backup}"
# Filesystem path used only for the "is the drive plugged in" check — on Git
# Bash the repo's `F:/…` form is not stat-able, hence the separate variable.
REPO_MOUNT="${DR_BACKUP_MOUNT:-/f/poindexter-backup}"
BOOTSTRAP="${POINDEXTER_HOME:-$HOME/.poindexter}/bootstrap.toml"
LOG_DIR="${POINDEXTER_HOME:-$HOME/.poindexter}/logs"
LOG_FILE="${LOG_DIR}/dr-backup-hourly.log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

SENTINEL_FILE="${LOG_DIR}/dr-backup-hourly-failed.sentinel"
notify_failure() {
    # Accept an explicit exit code as $1 (the chained EXIT trap runs `rm`
    # first, which clobbers $? to 0); fall back to $? when called directly.
    local rc=${1:-$?}
    [[ ${rc} -eq 0 ]] && { rm -f "${SENTINEL_FILE}" 2>/dev/null; return 0; }
    local secret_key token chat_id last_lines msg
    secret_key=$(grep -E '^poindexter_secret_key' "${BOOTSTRAP}" 2>/dev/null | cut -d'"' -f2 || true)
    chat_id=$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc \
        "SELECT value FROM app_settings WHERE key='telegram_chat_id' AND is_active=true" 2>/dev/null | tr -d '[:space:]')
    if [[ -n "${secret_key}" ]]; then
        token=$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc \
            "SELECT pgp_sym_decrypt(decode(substr(value, 8), 'base64'), '${secret_key}')::text \
             FROM app_settings WHERE key='telegram_bot_token' AND is_active=true" 2>/dev/null | tr -d '[:space:]')
    fi
    last_lines=$(tail -n 15 "${LOG_FILE}" 2>/dev/null | sed 's/[<>&]//g')
    if [[ -n "${token}" && -n "${chat_id}" ]]; then
        msg=$(printf '🚨 hourly pg backup FAILED (exit=%d) on %s\n\n%s' \
            "${rc}" "$(hostname)" "${last_lines}")
        curl -fsS --max-time 15 \
            --data-urlencode "chat_id=${chat_id}" \
            --data-urlencode "text=${msg}" \
            "https://api.telegram.org/bot${token}/sendMessage" > /dev/null 2>&1 \
            && return 0
    fi
    {
        echo "rc=${rc}"
        echo "ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "log=${LOG_FILE}"
        echo "tail<<EOF"
        echo "${last_lines}"
        echo "EOF"
    } > "${SENTINEL_FILE}" 2>/dev/null || true
    return 0
}
trap notify_failure EXIT

echo "================================================================"
echo "hourly pg snapshot: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"

RESTIC_PASSWORD=$(grep -E '^poindexter_backup_passphrase' "${BOOTSTRAP}" | cut -d'"' -f2)
[[ -z "${RESTIC_PASSWORD}" ]] && { echo "ERROR: passphrase missing"; exit 1; }
export RESTIC_PASSWORD

[[ -d "${REPO_MOUNT}" ]] || { echo "ERROR: backup repo not reachable at ${REPO_MOUNT} (is the drive plugged in?)"; exit 1; }

# Self-recover from stale restic locks left by killed prior runs (e.g.
# the 2026-05-07 unexpected shutdown that left a 21h+ stuck lock and
# made every prune step fail with exit code 11). `unlock` is a no-op
# when no stale lock exists; if a *running* process holds the lock,
# restic skips it instead of stealing. Closes Glad-Labs/poindexter#436.
echo "Checking for stale restic locks..."
"${RESTIC_BIN}" -r "${REPO}" unlock || true

# Skip if postgres-local container isn't running — no point alerting on
# a planned-down DB and creating false-positive pages. The
# host-TCP fallback below is for the OTHER scenario — Docker is
# completely dead (engine crash) and Postgres is also down with it,
# in which case we should fail loudly rather than dump nothing.
DOCKER_REACHABLE=true
if ! docker ps --filter name=poindexter-postgres-local --format '{{.Status}}' 2>/dev/null | grep -q '^Up'; then
    if docker info >/dev/null 2>&1; then
        echo "INFO: postgres-local not running but docker is — skipping hourly snapshot (planned downtime)"
        exit 0
    fi
    DOCKER_REACHABLE=false
fi

DUMP_DIR="$(mktemp -d)"
# Capture $? BEFORE rm (rm would clobber it to 0, so notify_failure's
# exit-code guard saw success and never alerted on a real failure), then pass
# the real code through explicitly. (#1294)
trap 'rc=$?; rm -rf "${DUMP_DIR}"; notify_failure "${rc}"' EXIT

# --- pg_dump: prefer docker exec, fall back to host TCP pg_dump when
# Docker is unreachable. The 2026-05-07 outage broke the daily script
# because Docker Desktop wasn't running at backup time — this hourly
# script lost backups for the same reason. Closes Glad-Labs/poindexter#437.
echo "Dumping poindexter_brain..."
if [[ "${DOCKER_REACHABLE}" = "true" ]]; then
    echo "INFO: docker reachable, using docker exec pg_dump"
    docker exec poindexter-postgres-local pg_dump \
        -U poindexter -d poindexter_brain \
        --no-owner --no-acl \
        > "${DUMP_DIR}/poindexter_brain.sql"
else
    echo "WARN: docker unavailable, falling back to host TCP pg_dump on localhost:15432"
    pg_password=$(grep -E '^local_postgres_password' "${BOOTSTRAP}" | cut -d'"' -f2)
    if [[ -z "${pg_password}" ]]; then
        echo "ERROR: local_postgres_password missing from ${BOOTSTRAP}"
        exit 1
    fi
    pg_dump_bin=""
    for candidate in /c/Program\ Files/PostgreSQL/*/bin/pg_dump.exe; do
        if [[ -x "${candidate}" ]]; then
            pg_dump_bin="${candidate}"
            break
        fi
    done
    if [[ -z "${pg_dump_bin}" ]]; then
        echo "ERROR: no pg_dump.exe found under /c/Program Files/PostgreSQL/*/bin/. Install postgresql client tools."
        exit 1
    fi
    echo "INFO: using ${pg_dump_bin}"
    PGPASSWORD="${pg_password}" "${pg_dump_bin}" \
        -h localhost -p 15432 -U poindexter -d poindexter_brain \
        --no-owner --no-acl \
        > "${DUMP_DIR}/poindexter_brain.sql"
fi

echo "Dump size: $(stat -c%s "${DUMP_DIR}/poindexter_brain.sql") bytes"

"${RESTIC_BIN}" -r "${REPO}" backup "${DUMP_DIR}" --tag "pg-hourly"

echo "Pruning old hourly snapshots (keep 24)..."
# --group-by "host,tags" is required because each run uses a fresh mktemp path.
# Without it restic treats every snapshot as a distinct group (host+tags+paths)
# and --keep-last 24 keeps 24 *per group* = keeps all of them forever.
"${RESTIC_BIN}" -r "${REPO}" forget --tag pg-hourly --group-by "host,tags" --keep-last 24 --prune

echo "================================================================"
echo "hourly pg snapshot complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"
