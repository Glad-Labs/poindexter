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
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    # Inside Docker — connect locally via socket
    DB_HOST="localhost"
    DB_PORT="5432"
    echo "[backup] Running inside Docker container"
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
