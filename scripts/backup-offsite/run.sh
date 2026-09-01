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
# Databases to dump, as a CSV. Defaults to the single database the runner
# covered before poindexter#891 so an existing install's behaviour (and its
# restic parent chain, which is keyed on --stdin-filename) is unchanged until
# an operator widens it. NOT the same thing as PG_DATABASE, which is the
# database this runner READS app_settings from and must stay a single name.
DEFAULT_DATABASES="poindexter_brain"
DEFAULT_VERIFY_INTERVAL_HOURS="168"
DEFAULT_VERIFY_SUBSET_PCT="5"
DEFAULT_RESTIC_HOST="poindexter"
# In-container paths whose presence in the newest `config` snapshot is what
# makes a restore actually usable (see verify_config_coverage).
DEFAULT_VERIFY_CONFIG_ARTIFACTS="/config/poindexter/bootstrap.toml"
# In-container mount points for the config surface (see run_config_backup).
# These are the paths the compose file binds ~/.poindexter and ~/.claude to,
# read-only — NOT host paths.
DEFAULT_CONFIG_PATHS="/config/poindexter,/config/claude"
# Everything derived, regenerable, or already covered elsewhere. Without
# these, ~/.poindexter alone is ~29 GB (Tier-1 dumps, rendered video, images,
# a full git clone) against ~50 KB of actual irreplaceable config.
DEFAULT_CONFIG_EXCLUDES="/config/poindexter/backups,/config/poindexter/video,/config/poindexter/generated-images,/config/poindexter/generated-videos,/config/poindexter/podcast,/config/poindexter/demo-clips,/config/poindexter/singer-venv,/config/poindexter/deploy,/config/poindexter/build,/config/poindexter/logs,/config/poindexter/*.log,/config/poindexter/*.log.1,/config/claude/shell-snapshots,/config/claude/session-env,/config/claude/cache,/config/claude/uploads"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# Set by run_backup: true once ANY database has been written to the repo this
# tick. tick() gates the config/verify/prune work on this rather than on
# "every database succeeded", because what those steps actually need is proof
# the repo is reachable and writable — one failing 15 MB database must not
# suppress the config tier or the coverage check.
BACKUP_ANY_OK=false

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

# Split a CSV app_setting into one trimmed, non-empty element per line.
#
# `printf '%s\n'` (not '%s') is load-bearing: without the trailing newline the
# final element is unterminated and `while read` silently DROPS it. Caught in
# review of the config-excludes loop, where it swallowed the last exclude
# pattern — i.e. the list quietly did less than it read as doing. Centralised
# here so a fourth CSV consumer cannot reintroduce it.
csv_to_lines() {
    printf '%s\n' "$1" | tr ',' '\n' \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$' || true
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
run_db_backup() {
    local repo="$1" source_tier="$2" restic_host="$3" db="$4"
    log "restic backup (streamed pg_dump -Z0 of ${db}) → ${repo} (host=${restic_host})"
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
        -U "${PG_USER}" -d "${db}" \
        --format=custom -Z0 \
        --no-owner --no-acl \
      | restic -r "${repo}" backup --stdin \
            --stdin-filename "${db}.dump" \
            --host "${restic_host}" \
            --tag poindexter --tag "${source_tier}" \
            2>"${err_file}" || rc=$?
    [[ -s "${err_file}" ]] && cat "${err_file}" >&2
    if [[ "${rc}" -eq 0 ]]; then
        log "offsite backup OK (${db})"
        emit_heartbeat "offsite_backup_succeeded" \
            "restic --stdin backup of ${db} complete"
        rm -f "${err_file}"
        return 0
    fi
    # Tail + strip newlines/`$` so a verbose retry sequence can't blow up the
    # alert row, and can't break emit_alert's `$$...$$`-quoted SQL literal.
    local err_tail
    err_tail=$(tail -c 600 "${err_file}" 2>/dev/null | tr '\n$' ' _')
    rm -f "${err_file}"
    log "offsite backup FAILED rc=${rc} (${db})"
    emit_alert "critical" \
        "Offsite restic backup failed for ${db} (rc=${rc})" \
        "streamed pg_dump -Z0 of ${db} | restic backup --stdin → ${repo} returned ${rc}. Check creds, network, postgres reachability, and the repo URL. restic stderr: ${err_tail:-<none captured>}"
    return "${rc}"
}

# Dump every database named by `offsite_backup_databases` (CSV) into the repo,
# one `restic backup --stdin` each (poindexter#891 fix 1).
#
# Before this, the tier streamed exactly one database, so a full-stack restore
# was impossible from the offsite copy alone. Each database gets its OWN
# `--stdin-filename "<db>.dump"`: restic picks a parent snapshot by
# (host, paths), so sharing one filename across databases would make each
# night's dump look like a total rewrite of the previous database's — the
# same dedup collapse the -Z0 fix was about, in a different disguise.
#
# One database failing does not stop the others: each alerts on its own and
# the loop continues, so a broken 15 MB database cannot cost you the 1.6 GB
# one. The return code is "everything succeeded"; BACKUP_ANY_OK is "the repo
# is reachable", which is what tick() needs to decide whether to continue.
run_backup() {
    local repo="$1" source_tier="$2" restic_host="${3:-${DEFAULT_RESTIC_HOST}}"
    local dbs_csv
    dbs_csv=$(read_setting offsite_backup_databases "${DEFAULT_DATABASES}")

    local -a dbs=()
    local db d seen
    while IFS= read -r db; do
        # Dedupe: a repeated entry dumps the same database twice per tick and
        # splits its parent chain across two identical paths.
        seen=0
        for d in ${dbs[@]+"${dbs[@]}"}; do
            if [[ "${d}" == "${db}" ]]; then seen=1; break; fi
        done
        if [[ "${seen}" -eq 0 ]]; then dbs+=("${db}"); fi
    done < <(csv_to_lines "${dbs_csv}")

    BACKUP_ANY_OK=false
    if [[ "${#dbs[@]}" -eq 0 ]]; then
        log "offsite backup: offsite_backup_databases resolved to nothing — dumped NOTHING"
        emit_alert "critical" \
            "Offsite backup has no source databases" \
            "offsite_backup_databases='${dbs_csv}' resolved to zero database names, so this tick dumped nothing at all while still reporting a completed run. Set it to a CSV of database names (the shipped default is '${DEFAULT_DATABASES}')."
        return 1
    fi

    local failed=0
    for db in "${dbs[@]}"; do
        if run_db_backup "${repo}" "${source_tier}" "${restic_host}" "${db}"; then
            BACKUP_ANY_OK=true
        else
            failed=$((failed + 1))
        fi
    done
    if [[ "${failed}" -gt 0 ]]; then
        log "offsite backup: ${failed}/${#dbs[@]} database(s) failed"
        return 1
    fi
    log "offsite backup: all ${#dbs[@]} database(s) OK"
    return 0
}

# Back up the machine-config surface (bootstrap.toml + the Claude memory
# tree) as a SECOND snapshot in the same repo (poindexter#889 fix 3).
#
# Why this exists: until 2026-08-27 the offsite tier shipped exactly one
# thing — a pg_dump of PG_DATABASE. Everything else that is irreplaceable
# (`~/.poindexter/bootstrap.toml`, which holds `poindexter_secret_key`, and
# `~/.claude/**/memory/`) was carried ONLY by the Tier-3 DR USB job
# (scripts/dr-backup/run-backup.sh). When that stick was pulled, every copy
# of bootstrap.toml collapsed onto one partition — and #889 is precisely the
# trap that springs then: lose `poindexter_secret_key` and the encrypted
# app_settings rows holding this repo's own restic password + S3 credentials
# cannot be decrypted, so the healthy DB snapshot above becomes unopenable.
#
# This does NOT by itself break the #889 cycle — opening the repo still
# needs the restic password held out-of-band. It closes the partial-loss
# case (lose the disk, still have the credentials), which is the common one.
# restic encrypts at rest, so shipping secret-bearing config here is
# defensible; it is the same content the USB tier already held.
#
# Separate snapshot, same repo: restic selects a parent by (host, paths), and
# these paths differ from the DB snapshot's pinned `--stdin-filename`, so the
# two lineages stay independent without needing a separate --host. Tagged
# `config` so `restic snapshots --tag config` isolates them.
#
# Failure here is warning-level and NON-fatal: the DB snapshot is the
# critical path and has already succeeded by the time we run, so a config
# hiccup must not suppress prune/verify. It must still alert, though — a
# silently-skipped config backup is the exact failure mode that motivated
# this function.
# Can this process actually READ the path (not merely see that it exists)?
# Directories need -x to traverse as well as -r. Split out as a named
# predicate so tests can exercise the unreadable BRANCH deterministically:
# CI runs as root, and root holds CAP_DAC_OVERRIDE, so a `chmod 000` fixture
# is readable there and cannot drive this case. (That same root-bypasses-DAC
# fact is what made the original mount verification for #3399 wrong.)
_config_path_readable() {
    local p="$1"
    if [[ -d "${p}" ]]; then
        [[ -r "${p}" && -x "${p}" ]]
        return
    fi
    [[ -r "${p}" ]]
}

run_config_backup() {
    local repo="$1" restic_host="$2"
    [[ "$(read_setting offsite_backup_config_enabled true)" == "true" ]] || {
        log "config backup disabled (offsite_backup_config_enabled) — skipping"
        return 0
    }

    local paths_csv excludes_csv
    paths_csv=$(read_setting offsite_backup_config_paths "${DEFAULT_CONFIG_PATHS}")
    excludes_csv=$(read_setting offsite_backup_config_excludes "${DEFAULT_CONFIG_EXCLUDES}")

    # Classify into readable / unreadable / missing. Existence is NOT enough:
    # this container runs as a fixed image uid, while the config surface is
    # 0700/0600 owned by the HOST user, so a uid mismatch makes a perfectly
    # mounted path unreadable. That case is worse than a missing mount —
    # restic exits 3 on unreadable content but STILL SAVES A SNAPSHOT
    # ("processed 0 files ... snapshot saved"), so `restic snapshots --tag
    # config` would list reassuring entries containing nothing. Verified
    # against restic 0.16.4. Directories need BOTH -r and -x (traverse).
    local -a present=() missing=() unreadable=()
    local p
    while IFS= read -r p; do
        [[ -z "${p}" ]] && continue
        if [[ ! -e "${p}" ]]; then
            missing+=("${p}")
        elif ! _config_path_readable "${p}"; then
            unreadable+=("${p}")
        else
            present+=("${p}")
        fi
    done < <(csv_to_lines "${paths_csv}")

    if [[ "${#missing[@]}" -gt 0 ]]; then
        log "WARN: config path(s) not mounted, skipping: ${missing[*]}"
    fi
    # Mounted-but-unreadable is a hard misconfiguration, not a soft skip: the
    # operator believes these paths are covered and they are not. Alert even
    # if other paths backed up fine, and never hand the path to restic (that
    # is what produces the empty-but-saved snapshot).
    if [[ "${#unreadable[@]}" -gt 0 ]]; then
        log "ERROR: config path(s) mounted but UNREADABLE by uid $(id -u): ${unreadable[*]}"
        emit_alert "warning" \
            "Offsite config backup cannot read mounted path(s)" \
            "Mounted but unreadable by container uid $(id -u): ${unreadable[*]}. The config surface is 0700/0600 owned by the host user, so this is almost certainly a uid mismatch — set the backup-offsite service's 'user:' to the host uid (POINDEXTER_HOST_UID). These paths are NOT backed up; bootstrap.toml may be among them."
    fi
    if [[ "${#present[@]}" -eq 0 ]]; then
        log "config backup: no configured paths present — skipping"
        emit_alert "warning" \
            "Offsite config backup found no paths" \
            "offsite_backup_config_paths='${paths_csv}' but none exist in the container. Check the read-only volume mounts on the backup-offsite service; the DB snapshot is unaffected."
        return 0
    fi

    local -a exclude_args=()
    while IFS= read -r p; do
        [[ -z "${p}" ]] && continue
        exclude_args+=(--exclude "${p}")
    done < <(csv_to_lines "${excludes_csv}")

    log "restic backup (config surface) ${present[*]} → ${repo} (host=${restic_host})"
    local rc=0 err_file
    err_file=$(mktemp)
    restic -r "${repo}" backup "${present[@]}" \
        "${exclude_args[@]}" \
        --host "${restic_host}" \
        --tag poindexter --tag config \
        2>"${err_file}" || rc=$?
    [[ -s "${err_file}" ]] && cat "${err_file}" >&2
    if [[ "${rc}" -eq 0 ]]; then
        log "offsite config backup OK"
        emit_heartbeat "offsite_config_backup_succeeded" \
            "restic backup of config surface complete (${#present[@]} path(s))"
        rm -f "${err_file}"
        return 0
    fi
    local err_tail
    err_tail=$(tail -c 600 "${err_file}" 2>/dev/null | tr '\n$' ' _')
    rm -f "${err_file}"
    log "offsite config backup FAILED rc=${rc}"
    emit_alert "warning" \
        "Offsite config backup failed (rc=${rc})" \
        "restic backup of ${present[*]} → ${repo} returned ${rc}. The DB snapshot succeeded; bootstrap.toml + memory are NOT covered this tick. restic stderr: ${err_tail:-<none captured>}"
    return 0
}

# Assert the recovery property; don't assume it (poindexter#891 fix 3).
#
# maybe_verify's `restic check` proves the repo's stored bytes are READABLE.
# It says nothing about whether the bytes that make recovery possible are IN
# it. Those are different claims, and only the second one expires: rotate a
# credential, re-run `poindexter setup`, drop a mount, or widen one glob in
# offsite_backup_config_excludes, and run_config_backup keeps saving cleanly
# while no longer carrying bootstrap.toml. `restic snapshots` still reports
# fresh daily snapshots — truthfully — so the repo goes on reporting healthy
# while the property it is trusted for has quietly stopped being true. That
# is the failure this function exists to catch: not "your backup stopped
# running", but "your backup stopped being sufficient".
#
# Why it is load-bearing: without bootstrap.toml's `poindexter_secret_key`,
# the encrypted app_settings rows inside the DB snapshot cannot be decrypted,
# so a "successful" restore hands the operator a partially-readable database
# — the #889 trap. A backup you believe is self-sufficient and isn't is worse
# than one you know is partial, because it retires the worry without retiring
# the risk.
#
# Runs EVERY tick, not on maybe_verify's weekly cadence: it costs one `ls`
# plus a dump of a ~5 KB file, and it is the assertion the whole tier exists
# to support.
#
# SECRETS: the artifacts are secret-bearing (master key, DB URL, service
# credentials). Contents are piped straight into sha256sum and are never
# logged, quoted into an alert, or written to a temp file. The 12-char hash
# PREFIXES are logged — a one-way digest is not content, and it is what makes
# "which version is stored" answerable when this fires.
verify_config_coverage() {
    local repo="$1" restic_host="$2"
    [[ "$(read_setting offsite_backup_verify_config_enabled true)" == "true" ]] || {
        log "config coverage verification disabled — skipping"
        return 0
    }
    local artifacts_csv
    artifacts_csv=$(read_setting offsite_backup_verify_config_artifacts \
        "${DEFAULT_VERIFY_CONFIG_ARTIFACTS}")

    # One listing for the whole snapshot, reused for every artifact. `latest`
    # resolves against the --tag/--host filters, so this is the same snapshot
    # run_config_backup just wrote.
    local listing rc=0
    listing=$(restic -r "${repo}" ls latest --tag config --host "${restic_host}" 2>/dev/null) || rc=$?
    if [[ "${rc}" -ne 0 || -z "${listing}" ]]; then
        log "config coverage: no readable config snapshot (rc=${rc})"
        emit_alert "critical" \
            "Offsite repo has no readable config snapshot" \
            "restic ls latest --tag config --host ${restic_host} on ${repo} returned rc=${rc} with no listing, so nothing in the offsite repo carries bootstrap.toml. A restore from this repo cannot decrypt the app_settings secrets inside the DB snapshot (#889). The database snapshots themselves are unaffected."
        return 1
    fi

    local artifact stored disk missing=0 drifted=0 checked=0
    while IFS= read -r artifact; do
        if [[ ! -e "${artifact}" ]]; then
            # Not mounted here — run_config_backup already alerts on that, and
            # verifying a path this container cannot see says nothing.
            log "config coverage: ${artifact} not present on disk — skipping"
            continue
        fi
        checked=$((checked + 1))
        # -x -F: whole-line, fixed-string. A path is not a regex.
        if ! printf '%s\n' "${listing}" | grep -qxF "${artifact}"; then
            log "config coverage: MISSING from newest config snapshot: ${artifact}"
            missing=$((missing + 1))
            emit_alert "critical" \
                "Offsite config snapshot is missing ${artifact}" \
                "The newest snapshot tagged 'config' on ${repo} does not contain ${artifact}, even though the file exists on disk and the config backup reported success. Check offsite_backup_config_excludes for a glob that now matches it, and offsite_backup_config_paths for a dropped mount. Until this is fixed a restore cannot decrypt app_settings secrets (#889)."
            continue
        fi
        # Contents go straight into sha256sum — never to a log, alert, or file.
        stored=$(restic -r "${repo}" dump latest --tag config --host "${restic_host}" \
            "${artifact}" 2>/dev/null | sha256sum | cut -c1-12) || stored=""
        disk=$(sha256sum < "${artifact}" | cut -c1-12) || disk=""
        if [[ -z "${stored}" || -z "${disk}" ]]; then
            log "config coverage: could not hash ${artifact} — treating as drift"
            drifted=$((drifted + 1))
            emit_alert "warning" \
                "Offsite config coverage could not hash ${artifact}" \
                "The file is listed in the newest 'config' snapshot on ${repo}, but reading it back (restic dump) or hashing the on-disk copy produced nothing, so the stored copy could not be compared to the live one. Coverage is unverified for this artifact."
            continue
        fi
        if [[ "${stored}" != "${disk}" ]]; then
            log "config coverage: DRIFT ${artifact} stored=${stored} disk=${disk}"
            drifted=$((drifted + 1))
            # warning, not critical: SOME recent copy exists, and the config
            # backup runs immediately before this check, so a single tick's
            # mismatch is usually a mid-tick edit that self-heals next tick.
            # A MISSING artifact is critical because it never self-heals.
            emit_alert "warning" \
                "Offsite config copy of ${artifact} is stale" \
                "The newest 'config' snapshot on ${repo} holds a different ${artifact} than the one on disk (stored sha256 ${stored}..., on-disk ${disk}...). The config backup runs immediately before this check, so a one-off is usually a mid-tick edit. If it persists past the next tick the offsite copy is genuinely stale, and a restore would return an outdated ${artifact} — an old poindexter_secret_key cannot decrypt current app_settings secrets."
            continue
        fi
        log "config coverage: ${artifact} present and current (sha256 ${disk}...)"
    done < <(csv_to_lines "${artifacts_csv}")

    # A check that scanned nothing has not passed.
    if [[ "${checked}" -eq 0 ]]; then
        log "config coverage: no configured artifacts present on disk — verified NOTHING"
        emit_alert "warning" \
            "Offsite config coverage verified nothing" \
            "offsite_backup_verify_config_artifacts='${artifacts_csv}' but none of those paths exist in this container, so the recovery property was not checked at all this tick. Either the mounts are wrong or the setting names paths that no longer exist."
        return 1
    fi
    if [[ "${missing}" -eq 0 && "${drifted}" -eq 0 ]]; then
        log "config coverage OK (${checked} artifact(s))"
        emit_heartbeat "offsite_config_coverage_verified" \
            "${checked} config artifact(s) present in newest config snapshot and matching on-disk"
        return 0
    fi
    return 1
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
    # Gate on BACKUP_ANY_OK, not on run_backup's rc: the rc means "every
    # database succeeded", while what the steps below need is only "the repo
    # is reachable and writable". With several databases configured, one
    # failing dump must not suppress the config tier or — especially — the
    # coverage check, which is what notices the repo has stopped being
    # sufficient. Each failed database has already alerted for itself.
    run_backup "${repo}" "${source_tier}" "${restic_host}" || true
    if [[ "${BACKUP_ANY_OK}" == "true" ]]; then
        # Non-fatal by contract (see run_config_backup) — it returns 0 even on
        # restic failure, so prune/verify still run against a good DB snapshot.
        run_config_backup "${repo}" "${restic_host}"
        # Immediately after the write, so the listing it reads is this tick's.
        verify_config_coverage "${repo}" "${restic_host}" || true
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
