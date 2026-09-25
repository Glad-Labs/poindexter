#!/usr/bin/env bash
# Poindexter in-stack backup runner.
#
# Tier 1 of the multi-tier backup design (poindexter#385). Lives inside
# a small alpine container. Loops forever — wakes every BACKUP_INTERVAL
# seconds, takes a pg_dump, prunes old dumps to the configured retention,
# logs to stdout (Docker captures), and inserts an alert_events row on
# any non-zero exit.
#
# Why a self-contained loop and not cron-in-container? Two reasons:
#   1. cron in alpine wants a TTY for stderr; getting it to log to
#      stdout cleanly requires more setup than the loop.
#   2. The BACKUP_INTERVAL is read from app_settings at each tick, so
#      operators can change cadence without container restart. cron's
#      crontab is static.
#
# Failure path: insert into alert_events with severity=critical, target
# the brain's alert_dispatcher poll (30s cadence). Same pipeline as
# Grafana alerts — one notification surface, not three.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_TIER="${BACKUP_TIER:-hourly}"
# Where the volumes tier finds its read-only per-volume mounts. Overridable
# for tests; the compose service always mounts at the /volumes default.
VOLUMES_MOUNT_ROOT="${VOLUMES_MOUNT_ROOT:-/volumes}"
PG_HOST="${PG_HOST:-postgres-local}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-poindexter}"
PG_DATABASE="${PG_DATABASE:-poindexter_brain}"
# PGPASSWORD is supplied via env from compose

# Interval and retention defaults — overridable via app_settings reads.
# `Nm` for minutes, `Nh` for hours, `Nd` for days. Internal helper parses.
DEFAULT_HOURLY_INTERVAL="1h"
DEFAULT_DAILY_INTERVAL="24h"
DEFAULT_VOLUMES_INTERVAL="24h"
DEFAULT_HOURLY_RETENTION=24
DEFAULT_DAILY_RETENTION=7
DEFAULT_VOLUMES_RETENTION=7

mkdir -p "${BACKUP_DIR}/${BACKUP_TIER}"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# ---------------------------------------------------------------------------
# Config helpers — read tunables from app_settings via psql. Failures fall
# back to compiled-in defaults so the service keeps running even if the DB
# is briefly unreachable (which is exactly when we'd most want backups
# to NOT stop firing).
# ---------------------------------------------------------------------------

read_setting() {
    local key="$1"
    local default="$2"
    local val
    val=$(PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -tAc \
        "SELECT value FROM app_settings WHERE key='${key}' AND is_active=true" \
        2>/dev/null | tr -d '[:space:]')
    [[ -z "${val}" ]] && val="${default}"
    printf '%s' "${val}"
}

# Convert "1h" / "30m" / "1d" → seconds. Falls back to default on parse error.
to_seconds() {
    local raw="$1"
    local default_secs="$2"
    local n unit
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

# ---------------------------------------------------------------------------
# Failure → alert_events row. Brain dispatcher fires Telegram/Discord on
# its next 30s sweep. Schema mirrors what Grafana's webhook handler writes
# so the dispatcher logic doesn't fork.
# ---------------------------------------------------------------------------

emit_alert() {
    local severity="$1"
    local summary="$2"
    local description="$3"
    # Schema (alert_events): alertname/severity/status/labels/annotations/
    # starts_at/fingerprint. summary + description live inside annotations
    # — that's where poindexter/brain/alert_dispatcher.py reads them.
    PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -v ON_ERROR_STOP=1 -tAc \
        "INSERT INTO alert_events (
            alertname, severity, status, labels, annotations,
            starts_at, fingerprint
         ) VALUES (
            'backup_${BACKUP_TIER}_failed',
            '${severity}',
            'firing',
            '{\"source\":\"backup-service\",\"tier\":\"${BACKUP_TIER}\",\"category\":\"backup\"}'::jsonb,
            jsonb_build_object('summary', \$\$${summary}\$\$, 'description', \$\$${description}\$\$),
            NOW(),
            'backup-${BACKUP_TIER}-' || EXTRACT(EPOCH FROM NOW())::bigint
         )" \
        2>&1 | tail -3 || log "WARN: alert insert failed (db unreachable?)"
}

# ---------------------------------------------------------------------------
# Volume tier (poindexter#890) — tars each non-Postgres named volume the
# compose service mounts read-only under /volumes/<label>, straight into
# ${BACKUP_DIR}/volumes/. Which volumes are protected is decided entirely by
# the compose file's mount list, not by this script — it just walks whatever
# it's handed, so adding a volume later is a compose-only change.
#
# Postgres data dirs are deliberately never mounted here: a tar of a live
# data directory is a torn snapshot that may not replay (pg_dump is the real
# artifact — tiers hourly/daily above already cover it).
# ---------------------------------------------------------------------------

run_volume_backup() {
    local vol_dir="${BACKUP_DIR}/volumes"
    mkdir -p "${vol_dir}"
    local ts; ts=$(date -u +%Y%m%dT%H%M%SZ)
    local overall_rc=0

    shopt -s nullglob
    local mounts=("${VOLUMES_MOUNT_ROOT}"/*/)
    shopt -u nullglob
    if [[ ${#mounts[@]} -eq 0 ]]; then
        log "FAIL: no mounts under ${VOLUMES_MOUNT_ROOT} — check the backup-volumes compose service definition"
        return 1
    fi

    local mount label tmp final src_files tar_rc tar_files
    for mount in "${mounts[@]}"; do
        label="$(basename "${mount%/}")"
        tmp="${vol_dir}/.${label}_${ts}.tar.gz.tmp"
        final="${vol_dir}/${label}_${ts}.tar.gz"

        # Count real files BEFORE tarring — the only way to tell "this volume
        # is genuinely empty" from "the mount failed and we archived nothing".
        # A size threshold can't: both produce a valid, tiny gzip, which is
        # exactly how three 0-byte tarballs sat undetected for months (#890).
        src_files=$(find "${mount}" -type f 2>/dev/null | wc -l | tr -d '[:space:]')

        tar_rc=0
        tar czf "${tmp}" --warning=no-file-changed -C "${mount}" . 2>&1 || tar_rc=$?
        # GNU tar exit 1 means "some file changed while being read" — expected
        # for live observability data (Loki/Prometheus/etc. write continuously)
        # and not by itself a reason to distrust the archive. Exit >=2 is fatal
        # (permission denied, disk full, ...). The file-count check below is
        # what actually decides whether the archive is trustworthy.
        if [[ "${tar_rc}" -ge 2 ]]; then
            log "FAIL ${label}: tar exited ${tar_rc} (fatal)"
            rm -f -- "${tmp}"
            overall_rc=1
            continue
        fi

        tar_files=$(tar -tzf "${tmp}" 2>/dev/null | grep -cv '/$' || true)
        if [[ "${src_files}" -gt 0 && "${tar_files}" -eq 0 ]]; then
            log "FAIL ${label}: source has ${src_files} file(s) but the archive captured none"
            rm -f -- "${tmp}"
            overall_rc=1
            continue
        fi

        mv "${tmp}" "${final}"
        log "OK ${label}: ${src_files} file(s), tar_rc=${tar_rc} -> $(basename "${final}")"
    done

    return "${overall_rc}"
}

prune_old_volumes() {
    local retention; retention=$(read_setting "backup_volumes_retention" "${DEFAULT_VOLUMES_RETENTION}")
    local vol_dir="${BACKUP_DIR}/volumes"
    log "pruning ${vol_dir} to last ${retention} archive(s) per volume"
    local label
    for label in $(
        ls -1 "${vol_dir}"/*.tar.gz 2>/dev/null \
            | sed -E 's#.*/([^/]+)_[0-9]{8}T[0-9]{6}Z\.tar\.gz$#\1#' \
            | sort -u
    ); do
        ls -1 "${vol_dir}/${label}"_*.tar.gz 2>/dev/null \
            | sort -r \
            | tail -n +$((retention + 1)) \
            | while IFS= read -r f; do
                log "  deleting ${f}"
                rm -f -- "${f}"
            done
    done
}

# ---------------------------------------------------------------------------
# pg_dump + retention. Uses --format=custom (compressed, restorable via
# pg_restore in parallel). Atomically renames after dump completes so a
# partial file from a mid-dump kill never gets prune-promoted.
# ---------------------------------------------------------------------------

run_dump() {
    if [[ "${BACKUP_TIER}" == "volumes" ]]; then
        run_volume_backup
        return $?
    fi

    local tier_dir="${BACKUP_DIR}/${BACKUP_TIER}"
    local ts; ts=$(date -u +%Y%m%dT%H%M%SZ)
    local tmp="${tier_dir}/.poindexter_brain_${ts}.dump.tmp"
    local final="${tier_dir}/poindexter_brain_${ts}.dump"

    log "starting pg_dump (tier=${BACKUP_TIER}) → ${final}"
    PGPASSWORD="${PGPASSWORD}" pg_dump \
        -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" \
        --format=custom \
        --no-owner --no-acl \
        --file="${tmp}"

    mv "${tmp}" "${final}"
    local size; size=$(stat -c%s "${final}")
    log "wrote ${final} (${size} bytes)"
}

prune_old() {
    if [[ "${BACKUP_TIER}" == "volumes" ]]; then
        prune_old_volumes
        return
    fi
    local retention
    if [[ "${BACKUP_TIER}" == "hourly" ]]; then
        retention=$(read_setting "backup_hourly_retention" "${DEFAULT_HOURLY_RETENTION}")
    else
        retention=$(read_setting "backup_daily_retention" "${DEFAULT_DAILY_RETENTION}")
    fi
    local tier_dir="${BACKUP_DIR}/${BACKUP_TIER}"
    log "pruning ${tier_dir} to last ${retention} dumps"
    # ls sorts alphabetically; timestamps make this chronological. Skip
    # the latest N, delete the rest. -P safe-guards against symlink chase.
    ls -1 "${tier_dir}"/poindexter_brain_*.dump 2>/dev/null \
        | sort -r \
        | tail -n +$((retention + 1)) \
        | while IFS= read -r f; do
            log "  deleting ${f}"
            rm -f -- "${f}"
        done
}

tick() {
    local enabled_key="backup_${BACKUP_TIER}_enabled"
    local enabled
    enabled=$(read_setting "${enabled_key}" "true")
    if [[ "${enabled}" != "true" ]]; then
        log "skipping ${BACKUP_TIER} tick (${enabled_key}=${enabled})"
        return 0
    fi
    if run_dump 2>&1; then
        log "${BACKUP_TIER} dump OK"
        prune_old
        return 0
    else
        local rc=$?
        log "${BACKUP_TIER} dump FAILED rc=${rc}"
        emit_alert "critical" \
            "Backup tier=${BACKUP_TIER} failed (rc=${rc})" \
            "pg_dump of ${PG_DATABASE} into ${BACKUP_DIR}/${BACKUP_TIER} returned ${rc}. Check container logs and disk space."
        return ${rc}
    fi
}

# When sourced (unit tests), expose the functions above without starting the
# service loop — same convention as scripts/backup-offsite/run.sh.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

# ---------------------------------------------------------------------------
# Main loop. One tick on boot, then sleep BACKUP_INTERVAL between ticks.
# ---------------------------------------------------------------------------

log "backup service starting (tier=${BACKUP_TIER}, dir=${BACKUP_DIR}/${BACKUP_TIER})"

# Wait for postgres to accept connections. pg_dump's own retry would
# emit alerts during normal stack-startup, which is noise.
until PGPASSWORD="${PGPASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" -c 'SELECT 1' >/dev/null 2>&1; do
    log "waiting for postgres at ${PG_HOST}:${PG_PORT}..."
    sleep 5
done
log "postgres reachable"

# Run an initial tick immediately so a fresh `docker compose up` produces
# the first dump within seconds, not BACKUP_INTERVAL minutes later.
tick || true

while true; do
    if [[ "${BACKUP_TIER}" == "hourly" ]]; then
        interval=$(read_setting "backup_hourly_interval" "${DEFAULT_HOURLY_INTERVAL}")
        sleep_secs=$(to_seconds "${interval}" 3600)
    elif [[ "${BACKUP_TIER}" == "volumes" ]]; then
        interval=$(read_setting "backup_volumes_interval" "${DEFAULT_VOLUMES_INTERVAL}")
        sleep_secs=$(to_seconds "${interval}" 86400)
    else
        interval=$(read_setting "backup_daily_interval" "${DEFAULT_DAILY_INTERVAL}")
        sleep_secs=$(to_seconds "${interval}" 86400)
    fi
    log "next ${BACKUP_TIER} tick in ${sleep_secs}s"
    sleep "${sleep_secs}"
    tick || true
done
