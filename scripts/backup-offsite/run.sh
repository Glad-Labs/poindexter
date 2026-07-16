#!/usr/bin/env bash
# Poindexter off-machine backup runner (Tier 2 — poindexter#386).
#
# Lives in the same alpine image as Tier 1 (scripts/Dockerfile.backup,
# which now bakes restic). Loops forever: each tick reads tunables from
# app_settings via psql, streams a fresh UNCOMPRESSED pg_dump into
# `restic backup --stdin` (so restic can actually dedupe + compress — see
# run_backup) against the configured S3-compatible repo, stamps an
# audit_log heartbeat, and — when due — runs `restic check`. On any
# failure it inserts an alert_events row (same schema as Tier 1) so the
# brain dispatcher pages.
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
DEFAULT_RESTIC_HOST="poindexter"

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
        "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - MAX(\"timestamp\")))::bigint, -1)
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

# Stream a fresh, UNCOMPRESSED pg_dump straight into restic via --stdin.
#
# Why not `restic backup <daily-dir>` (the pre-2026-07 behaviour)? Tier 1's
# dumps are `pg_dump --format=custom`, i.e. zlib-compressed. restic dedupes
# and compresses by content-defined chunking, and compressed bytes defeat
# both: a one-row change reshuffles the whole compressed stream, so every
# daily dump looked like 100% new data (measured 2026-07-11: 1.01x restic
# compression, ~150-230 MiB added per dump, repo at 8.3 GiB / 62 snapshots
# after 25 days — on track to breach the B2 10 GB free cap). Feeding restic
# an UNCOMPRESSED dump (-Z0) lets it dedupe the ~unchanged bulk day-over-day
# and compress its own packs, so growth drops sharply and the repo holds
# near the live DB size regardless of snapshot count. This is the documented
# `pg_dump | restic backup --stdin` pattern.
#
# We take a fresh dump here (the offsite runner already has psql/pg_dump
# connectivity — it reads app_settings every tick) rather than re-reading
# Tier 1's compressed files, so Tier 1's dumps + retention + restore-test
# are left completely untouched. `set -o pipefail` (global) makes the
# pipeline's rc reflect a pg_dump failure even when restic exits 0 on
# truncated input, so a half-streamed dump alerts + returns non-zero
# instead of silently saving a short snapshot.
run_backup() {
    local repo="$1" source_tier="$2" restic_host="${3:-${DEFAULT_RESTIC_HOST}}"
    log "restic backup (streamed pg_dump -Z0 of ${PG_DATABASE}) → ${repo} (host=${restic_host})"
    # Capture rc on the same statement: a fall-through `if` resets $? to 0,
    # which made the 2026-06-23 failure alert claim "rc=0" and return success.
    local rc=0
    # restic's own stderr (retries, the actual API error) is captured to a
    # file rather than left to flow straight through, so a failure alert can
    # quote it — the 2026-07-16 B2 storage-cap incident needed a docker logs
    # grep to learn the alert's "rc=1" actually meant "storage cap exceeded".
    # Re-emitted to our real stderr below regardless of outcome, so `docker
    # logs` still shows everything it did before this capture existed.
    local err_file
    err_file=$(mktemp)
    # -Z0 = no pg_dump-side compression (the whole point — restic compresses).
    # --stdin-filename pins the snapshot's single path so restic's
    # (host, paths) parent selection stays stable across container recreates,
    # the same reason --host is pinned: without both, each recreate logs "no
    # parent snapshot found" and re-ingests the full dump instead of a delta.
    PGPASSWORD="${PGPASSWORD}" pg_dump \
        -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DATABASE}" \
        --format=custom -Z0 \
        --no-owner --no-acl \
      | restic -r "${repo}" backup --stdin \
            --stdin-filename "poindexter_brain.dump" \
            --host "${restic_host}" \
            --tag poindexter --tag "${source_tier}" \
            2>"${err_file}" || rc=$?
    [[ -s "${err_file}" ]] && cat "${err_file}" >&2
    if [[ "${rc}" -eq 0 ]]; then
        log "offsite backup OK"
        emit_heartbeat "offsite_backup_succeeded" \
            "restic --stdin backup of ${PG_DATABASE} complete"
        rm -f "${err_file}"
        return 0
    fi
    # Tail + strip newlines/`$` so a verbose retry sequence can't blow up the
    # alert row, and can't break emit_alert's `$$...$$`-quoted SQL literal.
    local err_tail
    err_tail=$(tail -c 600 "${err_file}" 2>/dev/null | tr '\n$' ' _')
    rm -f "${err_file}"
    log "offsite backup FAILED rc=${rc}"
    emit_alert "critical" \
        "Offsite restic backup failed (rc=${rc})" \
        "streamed pg_dump -Z0 of ${PG_DATABASE} | restic backup --stdin → ${repo} returned ${rc}. Check creds, network, postgres reachability, and the repo URL. restic stderr: ${err_tail:-<none captured>}"
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
    # SigV4 region — restic signs with us-east-1 by default, which a
    # non-us-east-1 bucket (e.g. B2 us-east-005) rejects ("Signature validation
    # failed"). The wizard derives + stores this; export it so restic inherits.
    local region
    region=$(read_setting offsite_backup_s3_region "")
    if [[ -n "${region}" ]]; then
        export AWS_DEFAULT_REGION="${region}"
    fi
    source_tier=$(read_setting offsite_backup_source_tier "${DEFAULT_SOURCE_TIER}")
    local restic_host
    restic_host=$(read_setting offsite_backup_restic_host "${DEFAULT_RESTIC_HOST}")
    if run_backup "${repo}" "${source_tier}" "${restic_host}"; then
        maybe_prune "${repo}"
        maybe_verify "${repo}"
    fi
}

# When sourced (unit tests), expose the functions above without starting
# the service loop.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

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
