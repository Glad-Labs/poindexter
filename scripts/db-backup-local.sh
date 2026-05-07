#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Poindexter Local Database Backup Script
# Built by Glad Labs LLC
#
# Creates a compressed pg_dump of the local poindexter_brain database,
# saves to ~/.poindexter/backups/ with timestamped filenames, and
# prunes backups older than RETENTION_DAYS.
#
# Usage:
#   bash scripts/db-backup-local.sh              # from repo root
#   docker exec poindexter-postgres-local bash /scripts/db-backup-local.sh  # inside container
#
# The script auto-detects whether it's running inside Docker or on the
# host and adjusts the pg_dump connection accordingly.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-${HOME}/.poindexter/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DB_NAME="${LOCAL_POSTGRES_DB:-poindexter_brain}"
DB_USER="${LOCAL_POSTGRES_USER:-poindexter}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H%M%SZ")
BACKUP_FILE="poindexter-db-${TIMESTAMP}.dump"

# ── Detect environment ───────────────────────────────────────────────
# Prefer DATABASE_URL when present (set by docker-compose for the
# worker / brain). The legacy backup *sidecars* (poindexter-backup-*)
# share a network with postgres-local so ``localhost`` works there,
# but the worker container doesn't — DbBackupJob inside the worker
# would otherwise fail with "Connection refused" on every run.
if [ -n "${DATABASE_URL:-}" ]; then
    # Parse postgresql://user:pass@host:port/db -- bash-only, no python.
    # Drop the scheme prefix, then split user[:pass]@host[:port][/db].
    _url_no_scheme="${DATABASE_URL#postgresql://}"
    _url_no_scheme="${_url_no_scheme#postgres://}"
    _url_after_at="${_url_no_scheme#*@}"
    _userinfo="${_url_no_scheme%@*}"
    if [ "${_userinfo}" = "${_url_no_scheme}" ]; then
        _userinfo=""
    fi
    _hostport="${_url_after_at%%/*}"
    DB_HOST="${_hostport%%:*}"
    _port_part="${_hostport#*:}"
    if [ "${_port_part}" = "${_hostport}" ]; then
        DB_PORT="5432"
    else
        DB_PORT="${_port_part}"
    fi
    if [ -n "${_userinfo}" ]; then
        # _userinfo is user[:password]
        _u_user="${_userinfo%%:*}"
        if [ -n "${_u_user}" ]; then DB_USER="${_u_user}"; fi
        _u_pass_part="${_userinfo#*:}"
        if [ "${_u_pass_part}" != "${_userinfo}" ] && [ -z "${PGPASSWORD:-}" ]; then
            export PGPASSWORD="${_u_pass_part}"
        fi
    fi
    echo "[backup] Running with DATABASE_URL host=${DB_HOST} port=${DB_PORT} user=${DB_USER}"
elif [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    # Legacy sidecar path: shared-net backup containers reach postgres
    # via localhost on the shared loopback.
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    echo "[backup] Running inside Docker (no DATABASE_URL, using ${DB_HOST}:${DB_PORT})"
else
    # On the host — connect via mapped port
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-15432}"
    echo "[backup] Running on host (connecting to localhost:${DB_PORT})"
fi

# ── Ensure backup directory exists ────────────────────────────────────
mkdir -p "${BACKUP_DIR}"

# ── Run pg_dump ───────────────────────────────────────────────────────
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILE}"

echo "[backup] Starting pg_dump of ${DB_NAME}..."
echo "[backup] Output: ${BACKUP_PATH}"

if pg_dump \
    --host="${DB_HOST}" \
    --port="${DB_PORT}" \
    --username="${DB_USER}" \
    --dbname="${DB_NAME}" \
    --format=custom \
    --compress=6 \
    --no-password \
    --file="${BACKUP_PATH}" 2>&1; then

    # Verify the backup file
    if [ ! -s "${BACKUP_PATH}" ]; then
        echo "[backup] ERROR: Backup file is empty" >&2
        rm -f "${BACKUP_PATH}"
        exit 1
    fi

    FILE_SIZE=$(du -h "${BACKUP_PATH}" | cut -f1)
    echo "[backup] OK: ${BACKUP_FILE} (${FILE_SIZE})"
else
    echo "[backup] ERROR: pg_dump failed" >&2
    rm -f "${BACKUP_PATH}"
    exit 1
fi

# ── Verify backup is restorable ───────────────────────────────────────
echo "[backup] Verifying backup integrity..."
if pg_restore --list "${BACKUP_PATH}" > /dev/null 2>&1; then
    TABLES=$(pg_restore --list "${BACKUP_PATH}" 2>/dev/null | grep "TABLE " | wc -l)
    echo "[backup] OK: Backup contains ${TABLES} tables"
else
    echo "[backup] WARNING: pg_restore --list failed — backup may be corrupt" >&2
fi

# ── Retention: prune old backups ──────────────────────────────────────
echo "[backup] Pruning backups older than ${RETENTION_DAYS} days..."
PRUNED=0
find "${BACKUP_DIR}" -name "poindexter-db-*.dump" -mtime "+${RETENTION_DAYS}" -print -delete 2>/dev/null | while read -r f; do
    echo "[backup] Deleted: $(basename "${f}")"
    PRUNED=$((PRUNED + 1))
done

# ── Summary ───────────────────────────────────────────────────────────
TOTAL_BACKUPS=$(find "${BACKUP_DIR}" -name "poindexter-db-*.dump" 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)

echo "[backup] ══════════════════════════════════"
echo "[backup]   Latest: ${BACKUP_FILE} (${FILE_SIZE})"
echo "[backup]   Total:  ${TOTAL_BACKUPS} backups (${TOTAL_SIZE})"
echo "[backup]   Dir:    ${BACKUP_DIR}"
echo "[backup] ══════════════════════════════════"

# Output the backup path for callers to use
echo "BACKUP_PATH=${BACKUP_PATH}"
