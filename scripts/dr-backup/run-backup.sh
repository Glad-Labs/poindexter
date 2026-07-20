#!/usr/bin/env bash
# Glad Labs disaster-recovery backup.
#
# Scope: everything needed to rebuild the operator's workstation end-to-end:
#   - ~/.poindexter/                       (brain decisions, backups, scripts)
#   - ~/glad-labs-website/                 (source tree incl. .git)
#   - ~/glad-labs-prompts/                 (prompts repo incl. .git)
#   - ~/.claude/                           (operator memory + session history)
#   - pg_dump of poindexter_brain          (fresh each run, not the nightly JSON)
#
# ~/.claude was MISSING from this list until 2026-07-19 — a three-month hole.
# It holds the accumulated operator memory (decision log, user profile, ~50
# feedback + ~30 project memories, 352 memory .md files) plus session history.
# None of that is in git and none of it regenerates: it is the most
# irreplaceable thing on the box after bootstrap.toml, and it was the one
# precious tier the *living* backup didn't cover. The gap was invisible because
# the snapshot list looked healthy — it was, for the paths it knew about.
# Caches/transcript churn dedupe well, so the marginal cost is small.
#
# Target: F:\poindexter-backup (restic repo, encrypted with passphrase from
# ~/.poindexter/bootstrap.toml : poindexter_backup_passphrase).
#
# Retention: 7 daily / 4 weekly / 6 monthly snapshots. 128GB USB holds ~6 months
# comfortably; prune runs after every backup.
#
# Scheduled via Windows Task Scheduler — see register-task.ps1 in this dir.
# Fails loud (set -euo pipefail) — no try/except style swallowing.
#
# Failure notification: any non-zero exit fires a Telegram alert via the
# bot+chat configured in bootstrap.toml. This was added 2026-05-05 after
# 16 days of silent failures on the predecessor (Railway-targeted)
# db-backup.ps1 went unnoticed. If THIS script ever stops firing too,
# the absence of "DR backup complete" lines in the daily log is your
# canary — but the alert is the primary signal.

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
LOG_FILE="${LOG_DIR}/dr-backup.log"

mkdir -p "${LOG_DIR}"

# Tee all stdout+stderr into a log for audit/debugging. Task Scheduler
# also captures its own copy; this log is the durable one.
exec > >(tee -a "${LOG_FILE}") 2>&1

# Notify Telegram on any non-zero exit. The trap fires AFTER the last
# command's stderr lands in the log, so the alert can pull tail lines
# for context. Uses curl directly — no python/jq dep so the alert path
# stays alive even if the rest of the system is broken.
#
# Credentials live in app_settings (encrypted via pgcrypto with the
# secret_key from bootstrap.toml). The script decrypts them inline so
# operators don't have to copy-paste tokens into a second config file.
# If postgres is the thing that's down — that IS the failure we'd want
# to alert about, but we can't, so this falls back to writing a sentinel
# file the brain daemon's separate watchdog will eventually surface.
SENTINEL_FILE="${LOG_DIR}/dr-backup-failed.sentinel"
notify_failure() {
    # Accept an explicit exit code as $1 (chained EXIT traps run cleanup
    # commands first, which clobber $? to 0); fall back to $? when called
    # directly as a bare `trap notify_failure EXIT`. See the EXIT trap below.
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
    last_lines=$(tail -n 20 "${LOG_FILE}" 2>/dev/null | sed 's/[<>&]//g')
    if [[ -n "${token}" && -n "${chat_id}" ]]; then
        msg=$(printf '🚨 DR backup FAILED (exit=%d) on %s\n\nLast log lines:\n%s' \
            "${rc}" "$(hostname)" "${last_lines}")
        curl -fsS --max-time 15 \
            --data-urlencode "chat_id=${chat_id}" \
            --data-urlencode "text=${msg}" \
            "https://api.telegram.org/bot${token}/sendMessage" > /dev/null 2>&1 \
            && return 0
    fi
    # Telegram path failed (creds missing, postgres down, network broken).
    # Drop a sentinel for the brain daemon's backup_watchdog to find on
    # its next sweep — that's the second line of defense.
    {
        echo "rc=${rc}"
        echo "ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "host=$(hostname)"
        echo "log=${LOG_FILE}"
        echo "tail<<EOF"
        echo "${last_lines}"
        echo "EOF"
    } > "${SENTINEL_FILE}" 2>/dev/null || true
    return 0
}
trap notify_failure EXIT

echo "================================================================"
echo "DR backup run: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"

# --- Read restic passphrase from bootstrap.toml. Grep the key, trim to the
# quoted value. No fallback — if this fails, the backup fails loud, which
# is what we want.
RESTIC_PASSWORD=$(grep -E '^poindexter_backup_passphrase' "${BOOTSTRAP}" | cut -d'"' -f2)
if [[ -z "${RESTIC_PASSWORD}" ]]; then
    echo "ERROR: poindexter_backup_passphrase missing from ${BOOTSTRAP}"
    exit 1
fi
export RESTIC_PASSWORD

# --- Verify F:\ is reachable before starting. USB unplug is the most
# common failure mode — catch it up front instead of halfway through.
if [[ ! -d "${REPO_MOUNT}" ]]; then
    echo "ERROR: backup repo not reachable at ${REPO_MOUNT}. USB unplugged?"
    exit 1
fi

# --- Self-recover from stale restic locks left by killed prior runs.
# `unlock` is a no-op when no stale lock exists; if a *running* process
# holds the lock, restic skips it instead of stealing.
# Closes Glad-Labs/poindexter#436.
echo "Checking for stale restic locks..."
"${RESTIC_BIN}" -r "${REPO}" unlock || true

# --- pg_dump function: prefers docker exec (in-container, no auth needed
# beyond docker access), falls back to host-side TCP pg_dump when Docker
# is unavailable. The 2026-05-07 outage broke this script because Docker
# Desktop wasn't running at backup time; TCP fallback ensures DR backups
# keep running even when the engine is down — exactly the scenario where
# a fresh backup matters most. Closes Glad-Labs/poindexter#437.
do_pg_dump() {
    local out_file="$1"
    if docker exec poindexter-postgres-local true 2>/dev/null; then
        echo "INFO: docker reachable, using docker exec pg_dump"
        docker exec poindexter-postgres-local pg_dump \
            -U poindexter -d poindexter_brain \
            --no-owner --no-acl > "${out_file}"
        return 0
    fi
    echo "WARN: docker unavailable, falling back to host TCP pg_dump on localhost:15432"
    local pg_password
    pg_password=$(grep -E '^local_postgres_password' "${BOOTSTRAP}" | cut -d'"' -f2)
    if [[ -z "${pg_password}" ]]; then
        echo "ERROR: local_postgres_password missing from ${BOOTSTRAP}"
        return 1
    fi
    # Locate pg_dump — Git Bash doesn't have it on PATH, so glob the
    # PostgreSQL install dirs. Pinning to /c/Program Files/PostgreSQL/*
    # lets the script work across major versions (16, 17, 18 etc.) as
    # long as one is installed. pg_dump from a newer client against an
    # older server is officially supported for plain SQL output.
    local pg_dump_bin=""
    for candidate in /c/Program\ Files/PostgreSQL/*/bin/pg_dump.exe; do
        if [[ -x "${candidate}" ]]; then
            pg_dump_bin="${candidate}"
            break
        fi
    done
    if [[ -z "${pg_dump_bin}" ]]; then
        echo "ERROR: no pg_dump.exe found under /c/Program Files/PostgreSQL/*/bin/. Install postgresql client tools."
        return 1
    fi
    echo "INFO: using ${pg_dump_bin}"
    PGPASSWORD="${pg_password}" "${pg_dump_bin}" \
        -h localhost -p 15432 -U poindexter -d poindexter_brain \
        --no-owner --no-acl > "${out_file}"
}

# --- Fresh pg_dump of the local Postgres. Streams directly into a tmp
# file that restic will pick up in the same snapshot.
DUMP_DIR="$(mktemp -d)"
# Chain cleanup + failure-alerting in ONE trap. bash keeps a single EXIT
# trap, so this used to silently REPLACE the `trap notify_failure EXIT`
# above — meaning any failure after this line (~80% of the script, incl. the
# restic backup itself) exited with no Telegram alert and no sentinel. Capture
# $? FIRST (rm would otherwise clobber it to 0), clean up, then alert with the
# real code. Mirrors run-hourly-pg.sh. (#1294)
trap 'rc=$?; rm -rf "${DUMP_DIR}"; notify_failure "${rc}"' EXIT

echo "Dumping poindexter_brain..."
do_pg_dump "${DUMP_DIR}/poindexter_brain.sql"

DUMP_SIZE=$(stat -c%s "${DUMP_DIR}/poindexter_brain.sql")
echo "DB dump: ${DUMP_SIZE} bytes"

# --- Run restic. Each target appears as a separate path in the snapshot.
# Excludes are cheap filesystem-level skips, not restic's internal dedup.
# .poindexter/backups is excluded: this script creates its own fresh pg_dump
# above; backing up the in-container backup files too (~7 GB of hourly/daily
# pg_dumps + kuma + service snapshots) would double-count that data and eat
# USB space fast. The DR dump in ${DUMP_DIR} is the source of truth here.
echo "Running restic backup..."
# NOTE: the $HOME paths below MUST use double quotes. These were literal
# absolute paths before this script was parameterised, and single quotes were
# correct for those; they are wrong the moment the path contains a variable.
# Single-quoting one of these makes restic match the literal string "$HOME/..."
# against nothing — the exclude silently stops applying and several GB of
# superseded local backups ride into every snapshot, with no error. Caught
# during parameterisation, never shipped, but it is a one-character regression
# so it is called out here rather than left to be rediscovered.
"${RESTIC_BIN}" -r "${REPO}" backup \
    "$HOME/.poindexter" \
    "$HOME/glad-labs-website" \
    "$HOME/glad-labs-prompts" \
    "$HOME/.claude" \
    "${DUMP_DIR}" \
    --tag "dr-daily" \
    --exclude "$HOME/.poindexter/backups" \
    --exclude "$HOME/.claude/shell-snapshots" \
    --exclude "$HOME/.claude/session-env" \
    --exclude '**/__pycache__' \
    --exclude '**/node_modules' \
    --exclude '**/.next' \
    --exclude '**/.venv' \
    --exclude '**/venv' \
    --exclude '**/dist' \
    --exclude '**/build' \
    --exclude '**/.pytest_cache' \
    --exclude '**/.mypy_cache' \
    --exclude '**/.ruff_cache' \
    --exclude '**/singer-venvs' \
    --exclude '**/*.pyc'

# --- Retention: 7 daily / 4 weekly / 6 monthly snapshots, prune unreferenced
# blocks. restic --prune is O(N) over repo size but runs locally over USB —
# fast enough at this scale.
# --tag dr-daily scopes the forget to DR snapshots only (not pg-hourly).
# --group-by "host,tags" is required because ${DUMP_DIR} is a fresh mktemp
# path each run — without it every snapshot is its own group (host+tags+paths)
# and time-based policies never remove anything.
echo "Pruning old snapshots..."
"${RESTIC_BIN}" -r "${REPO}" forget \
    --tag dr-daily \
    --group-by "host,tags" \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 6 \
    --prune

# --- Repo stats for the log. Cheap query; useful to watch for drift.
echo "Repo stats:"
"${RESTIC_BIN}" -r "${REPO}" stats --mode raw-data

echo "================================================================"
echo "DR backup complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"
