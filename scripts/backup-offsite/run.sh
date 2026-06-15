#!/usr/bin/env bash
# Poindexter off-machine backup runner (Tier 2 — poindexter#386).
#
# Lives in the same alpine image as Tier 1 (scripts/Dockerfile.backup,
# which now bakes restic). Loops forever: each tick reads tunables from
# app_settings via psql, runs `restic backup` of the Tier 1 daily dump
# dir into the configured S3-compatible repo, stamps an audit_log
# heartbeat, and — when due — runs `restic check`. On any restic failure
# it inserts an alert_events row (same schema as Tier 1) so the brain
# dispatcher pages.
#
# SECRETS come from env (RESTIC_PASSWORD / AWS_ACCESS_KEY_ID /
# AWS_SECRET_ACCESS_KEY), materialized by start-stack.sh from encrypted
# app_settings into .poindexter-backup-offsite.env (the grafana-token
# pattern). NON-SECRET tunables (repo URL, cadence, …) come from
# app_settings via psql each tick. When the repo or password is empty the
# loop idles loudly (no error, no alert) — Tier 2 is opt-in.
#
# APPEND-ONLY: by default the runner only `restic backup`s (never
# forget/prune, both of which delete objects), so a write-only S3 key
# cannot destroy history. offsite_backup_prune_enabled=true opts into
# host-driven retention (requires a delete-capable key).

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PG_HOST="${PG_HOST:-postgres-local}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-poindexter}"
PG_DATABASE="${PG_DATABASE:-poindexter_brain}"
# PGPASSWORD supplied via env from compose.
# RESTIC_PASSWORD / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY supplied via
# env_file (.poindexter-backup-offsite.env). restic reads them directly.

DEFAULT_INTERVAL="24h"
DEFAULT_SOURCE_TIER="daily"
DEFAULT_VERIFY_INTERVAL_HOURS="168"
DEFAULT_VERIFY_SUBSET_PCT="5"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# --- app_settings reads (copy of scripts/backup/run.sh::read_setting) -------
read_setting() {
    local key="$1" default="$2" val
    val=$(PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -tAc \
        "SELECT value FROM app_settings WHERE key='${key}' AND is_active=true" \
        2>/dev/null | tr -d '[:space:]')
    [[ -z "${val}" ]] && val="${default}"
    printf '%s' "${val}"
}

# Convert "24h"/"30m"/"1d" → seconds (copy of scripts/backup/run.sh::to_seconds).
to_seconds() {
    local raw="$1" default_secs="$2" n unit
    if [[ "${raw}" =~ ^([0-9]+)([smhd])$ ]]; then
        n="${BASH_REMATCH[1]}"; unit="${BASH_REMATCH[2]}"
        case "${unit}" in
            s) printf '%d' "${n}" ;;
            m) printf '%d' $((n * 60)) ;;
            h) printf '%d' $((n * 3600)) ;;
            d) printf '%d' $((n * 86400)) ;;
        esac
    else
        printf '%d' "${default_secs}"
    fi
}

# Seconds since the newest audit_log event of a given type, or -1 if none.
seconds_since_event() {
    local event="$1"
    PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -tAc \
        "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - MAX(created_at)))::bigint, -1)
         FROM audit_log WHERE event_type='${event}'" \
        2>/dev/null | tr -d '[:space:]'
}

emit_heartbeat() {
    local event="$1" detail="$2"
    PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -tAc \
        "INSERT INTO audit_log (event_type, source, details, severity)
         VALUES ('${event}', 'backup-offsite',
                 jsonb_build_object('detail', \$\$${detail}\$\$), 'info')" \
        >/dev/null 2>&1 || log "WARN: heartbeat insert failed (db unreachable?)"
}

# Failure → alert_events (copy of scripts/backup/run.sh::emit_alert, retargeted).
emit_alert() {
    local severity="$1" summary="$2" description="$3"
    PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -v ON_ERROR_STOP=1 -tAc \
        "INSERT INTO alert_events (
            alertname, severity, status, labels, annotations, starts_at, fingerprint
         ) VALUES (
            'offsite_backup_failed', '${severity}', 'firing',
            '{\"source\":\"backup-offsite\",\"category\":\"backup\",\"tier\":\"offsite\"}'::jsonb,
            jsonb_build_object('summary', \$\$${summary}\$\$, 'description', \$\$${description}\$\$),
            NOW(), 'offsite-backup-' || EXTRACT(EPOCH FROM NOW())::bigint
         )" \
        2>&1 | tail -3 || log "WARN: alert insert failed (db unreachable?)"
}

run_backup() {
    local repo="$1" source_tier="$2" src="${BACKUP_DIR}/${source_tier}"
    if [[ ! -d "${src}" ]]; then
        log "source dir ${src} missing — nothing to back up yet"
        return 0
    fi
    log "restic backup ${src} → ${repo}"
    if restic -r "${repo}" backup "${src}" --tag poindexter --tag "${source_tier}"; then
        log "offsite backup OK"
        emit_heartbeat "offsite_backup_succeeded" "restic backup of ${src} complete"
        return 0
    fi
    local rc=$?
    log "offsite backup FAILED rc=${rc}"
    emit_alert "critical" \
        "Offsite restic backup failed (rc=${rc})" \
        "restic backup of ${src} → ${repo} returned ${rc}. Check creds, network, and the repo URL."
    return "${rc}"
}

maybe_prune() {
    local repo="$1"
    [[ "$(read_setting offsite_backup_prune_enabled false)" == "true" ]] || return 0
    local kd kw km
    kd=$(read_setting offsite_backup_keep_daily 7)
    kw=$(read_setting offsite_backup_keep_weekly 4)
    km=$(read_setting offsite_backup_keep_monthly 6)
    log "restic forget --prune (keep d=${kd} w=${kw} m=${km})"
    restic -r "${repo}" forget --keep-daily "${kd}" --keep-weekly "${kw}" \
        --keep-monthly "${km}" --prune || \
        emit_alert "warning" "Offsite prune failed" \
            "restic forget --prune failed — is the key delete-capable? Append-only keys cannot prune."
}

maybe_verify() {
    local repo="$1"
    [[ "$(read_setting offsite_backup_verify_enabled true)" == "true" ]] || return 0
    local iv pct since
    iv=$(read_setting offsite_backup_verify_interval_hours "${DEFAULT_VERIFY_INTERVAL_HOURS}")
    pct=$(read_setting offsite_backup_verify_read_data_subset_percent "${DEFAULT_VERIFY_SUBSET_PCT}")
    since=$(seconds_since_event "offsite_backup_verified")
    # -1 ⇒ never verified ⇒ due. Otherwise compare to interval.
    if [[ "${since}" != "-1" && -n "${since}" && "${since}" -lt $((iv * 3600)) ]]; then
        return 0
    fi
    log "restic check --read-data-subset=${pct}% (last verify ${since}s ago)"
    if restic -r "${repo}" check --read-data-subset="${pct}%"; then
        log "offsite verify OK"
        emit_heartbeat "offsite_backup_verified" "restic check ${pct}% subset clean"
    else
        local rc=$?
        log "offsite verify FAILED rc=${rc}"
        emit_alert "critical" \
            "Offsite restic check failed (rc=${rc})" \
            "restic check --read-data-subset=${pct}% on ${repo} returned ${rc} — possible corruption/bit-rot in the remote repo."
    fi
}

tick() {
    local enabled repo source_tier
    enabled=$(read_setting offsite_backup_enabled true)
    if [[ "${enabled}" != "true" ]]; then
        log "offsite backup disabled (offsite_backup_enabled=${enabled}) — idling"
        return 0
    fi
    repo=$(read_setting offsite_backup_repository "")
    if [[ -z "${repo}" || -z "${RESTIC_PASSWORD:-}" ]]; then
        log "offsite backup not configured (repo/password empty) — run \`poindexter backup setup\`. Idling."
        return 0
    fi
    source_tier=$(read_setting offsite_backup_source_tier "${DEFAULT_SOURCE_TIER}")
    if run_backup "${repo}" "${source_tier}"; then
        maybe_prune "${repo}"
        maybe_verify "${repo}"
    fi
}

log "offsite backup service starting (dir=${BACKUP_DIR})"
until PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -c 'SELECT 1' >/dev/null 2>&1; do
    log "waiting for postgres at ${PG_HOST}:${PG_PORT}..."
    sleep 5
done
log "postgres reachable"

tick || true
while true; do
    interval=$(read_setting offsite_backup_interval "${DEFAULT_INTERVAL}")
    sleep_secs=$(to_seconds "${interval}" 86400)
    log "next offsite tick in ${sleep_secs}s"
    sleep "${sleep_secs}"
    tick || true
done
