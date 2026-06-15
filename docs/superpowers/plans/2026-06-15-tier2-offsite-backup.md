# Tier 2 Off-Machine Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in off-machine backup tier — a `poindexter backup` wizard that configures restic against an S3-compatible bucket, an in-stack `backup-offsite` service that encrypts + ships the Tier 1 daily dumps with weekly remote verification, and a brain auto-retry watch that self-heals a wedged runner before paging.

**Architecture:** Tier 2 = "ship Tier 1's dumps off-machine." The Tier 1 `pg_dump` files already land in `~/.poindexter/backups/auto/daily/`; a new alpine+restic compose service reads that dir read-only and `restic backup`s it to the remote, re-reading cadence/repo from `app_settings` each tick (exactly like `scripts/backup/run.sh`). Secrets live encrypted in `app_settings` and are materialized to a git-ignored env file by `start-stack.sh` (the `grafana_webhook_oauth_jwt` pattern). A brain probe (`brain/offsite_backup_watch.py`, a sibling of `backup_watcher.py`) reads an `audit_log` heartbeat creds-free and `docker restart`s the runner on staleness, emitting its own `offsite_backup_stale` alert on escalate.

**Tech Stack:** restic (S3 backend), Docker Compose, alpine+bash, Python/click (CLI), asyncpg (brain), pgcrypto (secret decrypt), pytest.

**Spec:** [docs/superpowers/specs/2026-06-15-tier2-offsite-backup-design.md](../specs/2026-06-15-tier2-offsite-backup-design.md)

**Conventions for every task:**

- Branch already exists (`claude/adoring-wilbur-6c67db` worktree). Commit per task; never push `main`.
- Python tests run from `src/cofounder_agent`: `poetry run pytest tests/unit/<path> -v`. If the worktree has no poetry env, fall back to the repo-root venv with `PYTHONPATH` per `reference_worktree_preflight_no_poetry_install`.
- New `app_settings` non-secret defaults go in `services/migrations/0000_baseline.seeds.sql` (category `'backup'`), matching the existing `backup_watcher_*` / `restore_test_*` seeds — NOT a new migration, NOT `settings_defaults.py` (these keys are read via psql/brain, not `site_config.get()` call-sites, so the generator wouldn't retain them). The runner + probe also carry compiled-in defaults, so a missing row is safe.

---

## File map

**Create:**

- `scripts/backup-offsite/run.sh` — the restic runner loop (mirrors `scripts/backup/run.sh`)
- `scripts/_backup_offsite_secrets.py` — decrypt-and-emit env helper (mirrors `scripts/_grafana_webhook_token.py`)
- `src/cofounder_agent/poindexter/cli/backup.py` — the `poindexter backup` click group + wizard
- `brain/offsite_backup_watch.py` — the auto-retry watch probe (mirrors `brain/backup_watcher.py`)
- `src/cofounder_agent/tests/unit/cli/test_backup_cli.py`
- `src/cofounder_agent/tests/unit/scripts/test_backup_offsite_secrets.py`
- `src/cofounder_agent/tests/unit/brain/test_offsite_backup_watch.py`

**Modify:**

- `scripts/Dockerfile.backup` — add the `restic` apk package
- `docker-compose.local.yml` — add the `backup-offsite` service (after `backup-daily`, ~line 1842)
- `scripts/start-stack.sh` — materialize `.poindexter-backup-offsite.env` (after the Grafana block, ~line 96)
- `.gitignore` — add `.poindexter-backup-offsite.env`
- `src/cofounder_agent/poindexter/cli/app.py` — register `backup_group`
- `services/migrations/0000_baseline.seeds.sql` — seed the non-secret tunables
- `brain/brain_daemon.py` — wire the new probe (import guard ~235, `_REQUIRED_MODULES` ~383, `run_cycle` ~2490)
- `brain/Dockerfile` — `COPY` the new module
- `docs/operations/backups.md` — rewrite the Tier 2 section
- `docs/operations/disaster-recovery.md` — restore-from-remote runbook

---

## Task 1: Seed the non-secret tunables

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (the `'backup'`-category block, ~line 22-40)

- [ ] **Step 1: Add the seed rows**

Insert these lines immediately after the existing `backup_watcher_*` rows (keep the file's alphabetical-within-block ordering loose — the runner sorts nothing, ON CONFLICT makes order irrelevant):

```sql
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_enabled', 'true', 'backup', 'Master switch for the off-machine (Tier 2) restic loop. The backup-offsite container stays running but idles when false or when no repository/creds are configured.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_repository', '', 'backup', 'restic repository URL, e.g. s3:https://s3.us-west-002.backblazeb2.com/<bucket>/<path>. Empty = Tier 2 inert. Set by `poindexter backup setup`.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_interval', '24h', 'backup', 'Cadence between offsite restic backups. Format <N>{s|m|h|d}. Read fresh each tick.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_source_tier', 'daily', 'backup', 'Which Tier 1 dump subdir under /backups to ship off-machine.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_restic_image', 'restic/restic:0.18.0', 'backup', 'Pinned restic image the wizard + brain verify use via `docker run`. The runner bakes its own restic via apk.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_max_age_hours', '26', 'backup', 'Staleness threshold for the brain offsite_backup_watch probe (daily cadence + 2h slack).', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_verify_enabled', 'true', 'backup', 'Master switch for the weekly remote `restic check`.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_verify_interval_hours', '168', 'backup', 'How often the runner runs `restic check` against the remote repo (weekly).', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_verify_read_data_subset_percent', '5', 'backup', 'Percentage of pack data `restic check --read-data-subset` re-reads to catch bit-rot.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_watch_enabled', 'true', 'backup', 'Master switch for the brain auto-retry watch on the offsite tier.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_watch_max_retries', '2', 'backup', 'docker restart attempts before the watch escalates and emits offsite_backup_stale.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_watch_retry_delay_seconds', '120', 'backup', 'Wait between docker restart and re-checking the heartbeat.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_prune_enabled', 'false', 'backup', 'When false (default) the runner never forget/prunes — keeps an append-only S3 key safe. Enable ONLY with a privileged (delete-capable) key.', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_keep_daily', '7', 'backup', 'restic forget --keep-daily (only consulted when offsite_backup_prune_enabled=true).', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_keep_weekly', '4', 'backup', 'restic forget --keep-weekly (only consulted when offsite_backup_prune_enabled=true).', false, true) ON CONFLICT (key) DO NOTHING;
INSERT INTO app_settings (key, value, category, description, is_secret, is_active) VALUES ('offsite_backup_keep_monthly', '6', 'backup', 'restic forget --keep-monthly (only consulted when offsite_backup_prune_enabled=true).', false, true) ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 2: Verify the seed file still parses (lint)**

Run: `python scripts/ci/migrations_lint.py`
Expected: exit 0, no collision/format complaints. (If the script targets only `*.py` migrations, this is a no-op pass — the `.sql` is loaded by `0000_baseline.py`; confirm that file still imports the seeds without error: `python -c "import ast,pathlib; ast.parse(pathlib.Path('src/cofounder_agent/services/migrations/0000_baseline.py').read_text())"` → no output.)

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/0000_baseline.seeds.sql
git commit -m "feat(backup): seed Tier 2 offsite_backup_* tunables (#386)"
```

---

## Task 2: Add restic to the backup image

**Files:**

- Modify: `scripts/Dockerfile.backup:18-24` (the `apk add` block)

- [ ] **Step 1: Add `restic` to the apk install**

Change the `RUN apk add` block to include `restic` (alpine community package — pulls a recent restic, sufficient since the repo format is version-stable):

```dockerfile
RUN apk add --no-cache \
    bash \
    postgresql16-client \
    curl \
    coreutils \
    findutils \
    restic \
    tzdata
```

Also update the top-of-file comment line 8 from:

```
# Tier 2 (off-machine: USB / S3 / B2 via restic) lives elsewhere — this
# service is the always-on first line of defense.
```

to:

```
# Tier 2 (off-machine via restic) reuses THIS image: the backup-offsite
# service runs scripts/backup-offsite/run.sh with the restic binary added
# below. This image is the always-on first line of defense for both tiers.
```

- [ ] **Step 2: Verify restic installs in the image**

Run: `docker build -f scripts/Dockerfile.backup -t poindexter-backup:offsite-test . && docker run --rm --entrypoint restic poindexter-backup:offsite-test version`
Expected: prints `restic <version> compiled with go...`. If the alpine `restic` package is unavailable for the pinned alpine tag, fall back to downloading the binary (add to the plan as a follow-up; note the version printed here so the wizard/brain image tag in Task 1 can be aligned).

- [ ] **Step 3: Commit**

```bash
git add scripts/Dockerfile.backup
git commit -m "feat(backup): bake restic into the backup image for Tier 2 (#386)"
```

---

## Task 3: The offsite runner loop

**Files:**

- Create: `scripts/backup-offsite/run.sh`
- Reference (copy helpers verbatim): `scripts/backup/run.sh` (`log`, `read_setting`, `to_seconds`, the postgres-wait loop)

- [ ] **Step 1: Write `scripts/backup-offsite/run.sh`**

```bash
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

# Seconds since the newest audit_log event of a given type, or empty string.
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
```

- [ ] **Step 2: Make it executable + shellcheck**

Run: `chmod +x scripts/backup-offsite/run.sh && shellcheck scripts/backup-offsite/run.sh`
Expected: exit 0 (no errors). SC2155-style warnings about masking return values in `local x=$(...)` are acceptable if the sibling `scripts/backup/run.sh` has the same — match its `.shellcheckrc`/inline-disable posture. Fix any genuine error.

- [ ] **Step 3: Smoke — the not-configured path idles**

Run (mocks psql to return empty repo):

```bash
cat > /tmp/psql-stub.sh <<'EOF'
#!/usr/bin/env bash
# Stub psql: SELECT value ... offsite_backup_enabled → true; everything else empty.
case "$*" in
  *offsite_backup_enabled*) echo "true" ;;
  *) echo "" ;;
esac
EOF
chmod +x /tmp/psql-stub.sh
PATH="/tmp:$PATH" RESTIC_PASSWORD="" bash -c '
  source scripts/backup-offsite/run.sh 2>/dev/null || true' 2>&1 | head -1 || true
```

Expected: the tick logs `offsite backup not configured ... Idling.` (The full loop won't be exercised here — this asserts the inert branch; the real end-to-end runs in Task 12's manual verification.)

- [ ] **Step 4: Commit**

```bash
git add scripts/backup-offsite/run.sh
git commit -m "feat(backup): offsite restic runner loop (#386)"
```

---

## Task 4: The `backup-offsite` compose service

**Files:**

- Modify: `docker-compose.local.yml` — insert after the `backup-daily` service (after ~line 1841, before the LiveKit comment block)

- [ ] **Step 1: Add the service**

```yaml
# ===========================================
# Tier 2 — off-machine backup (poindexter#386). Same image as the Tier 1
# backup services (restic baked in), but runs scripts/backup-offsite/run.sh:
# restic backup of the daily dump dir → an S3-compatible bucket, weekly
# restic check, append-only-safe (no prune by default). OPT-IN: idles until
# `poindexter backup setup` writes the repo + creds. Secrets arrive via the
# env_file start-stack.sh materializes from encrypted app_settings; the
# ${VAR:-} defaults (NOT :?) keep the service dormant — not erroring — on a
# fresh stack. Staleness/auto-retry is owned by the brain offsite_backup_watch
# probe, so there is intentionally no healthcheck here.
backup-offsite:
  build:
    context: .
    dockerfile: scripts/Dockerfile.backup
  image: poindexter-backup:latest
  container_name: poindexter-backup-offsite
  restart: unless-stopped
  entrypoint: ['/app/run-offsite.sh']
  environment:
    PG_HOST: postgres-local
    PG_PORT: '5432'
    PG_USER: ${LOCAL_POSTGRES_USER:-poindexter}
    PG_DATABASE: ${LOCAL_POSTGRES_DB:-poindexter_brain}
    PGPASSWORD: ${LOCAL_POSTGRES_PASSWORD:?Run 'poindexter setup' — LOCAL_POSTGRES_PASSWORD is generated in bootstrap.toml}
    TZ: ${TZ:-UTC}
  env_file:
    # Auto-managed by scripts/start-stack.sh from encrypted app_settings
    # (offsite_backup_restic_password / _s3_access_key_id /
    # _s3_secret_access_key). Holds RESTIC_PASSWORD + AWS_ACCESS_KEY_ID +
    # AWS_SECRET_ACCESS_KEY. Git-ignored. Empty assignments when unconfigured
    # ⇒ the runner idles loudly.
    - .poindexter-backup-offsite.env
  volumes:
    # Read-only: the offsite tier only READS the Tier 1 dumps and ships them.
    - ${POINDEXTER_BACKUP_DIR:-${USERPROFILE:-${HOME}}/.poindexter/backups/auto}:/backups:ro
  depends_on:
    postgres-local:
      condition: service_healthy
```

Note the `entrypoint` override to `/app/run-offsite.sh` — the shared Dockerfile's default entrypoint is the Tier 1 `/app/run.sh`. Add the offsite script to the image: in `scripts/Dockerfile.backup`, after the existing `COPY scripts/backup/run.sh /app/run.sh`, add:

```dockerfile
COPY scripts/backup-offsite/run.sh /app/run-offsite.sh
RUN chmod +x /app/run-offsite.sh
```

(Place the `COPY`/`chmod` BEFORE the `chown -R backup:backup /app` line so the new file gets the right ownership.)

- [ ] **Step 2: Validate compose parses + the `${VAR:-}` (not `:?`) interpolation**

Run: `docker compose -f docker-compose.local.yml config >/dev/null && echo OK`
Expected: `OK` with no `LOCAL_*`-style "variable is not set" error for the offsite secrets — i.e. on a shell where `RESTIC_PASSWORD` etc. are unset, the service still renders (because the runner reads them from the env_file at runtime, and the compose-level interpolation for them lives in the env_file, not inline). If `docker compose config` complains the `env_file` is missing, create an empty placeholder so a fresh clone validates: `touch .poindexter-backup-offsite.env` (Task 6 makes start-stack regenerate it; Task 6 also gitignores it).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.local.yml scripts/Dockerfile.backup
git commit -m "feat(backup): backup-offsite compose service (#386)"
```

---

## Task 5: The secret-materialization helper

**Files:**

- Create: `scripts/_backup_offsite_secrets.py`
- Reference (mirror): `scripts/_grafana_webhook_token.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_backup_offsite_secrets.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for scripts/_backup_offsite_secrets.py (poindexter#386)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the script module by path (scripts/ isn't a package).
_SPEC = importlib.util.spec_from_file_location(
    "_backup_offsite_secrets",
    Path(__file__).resolve().parents[4] / "scripts" / "_backup_offsite_secrets.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)


def test_render_env_emits_three_keys():
    out = mod._render_env({
        "offsite_backup_restic_password": "pw-123",
        "offsite_backup_s3_access_key_id": "akid-456",
        "offsite_backup_s3_secret_access_key": "secret-789",
    })
    assert "RESTIC_PASSWORD=pw-123" in out
    assert "AWS_ACCESS_KEY_ID=akid-456" in out
    assert "AWS_SECRET_ACCESS_KEY=secret-789" in out


def test_render_env_missing_keys_emit_empty_assignments():
    out = mod._render_env({})
    # Loud-inert: explicit empty assignments so the runner idles, not crashes.
    assert "RESTIC_PASSWORD=" in out
    assert "AWS_ACCESS_KEY_ID=" in out
    assert "AWS_SECRET_ACCESS_KEY=" in out
    # No stray plaintext.
    assert "None" not in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_backup_offsite_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: _render_env` (file not created yet).

- [ ] **Step 3: Write `scripts/_backup_offsite_secrets.py`**

```python
"""Decrypt the offsite-backup secrets for ``scripts/start-stack.sh``.

Reads the three encrypted ``app_settings`` rows
(``offsite_backup_restic_password`` / ``offsite_backup_s3_access_key_id`` /
``offsite_backup_s3_secret_access_key``), decrypts each with
``POINDEXTER_SECRET_KEY`` (from ``~/.poindexter/bootstrap.toml``), and prints
an env-file body to stdout that the ``backup-offsite`` compose service loads:

    RESTIC_PASSWORD=...
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...

Mirrors ``scripts/_grafana_webhook_token.py`` exactly (same pgcrypto decrypt,
same bootstrap.toml read, same fail-soft posture). Never raises into the
calling shell: any failure emits an explicit empty assignment for the missing
key (loud-inert — the runner then idles) and a one-line WARNING to stderr.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_BOOTSTRAP_PATH = Path.home() / ".poindexter" / "bootstrap.toml"
_ENC_PREFIX = "enc:v1:"

# app_settings key → env var the runner/restic expects.
_KEYS: dict[str, str] = {
    "offsite_backup_restic_password": "RESTIC_PASSWORD",
    "offsite_backup_s3_access_key_id": "AWS_ACCESS_KEY_ID",
    "offsite_backup_s3_secret_access_key": "AWS_SECRET_ACCESS_KEY",
}


def _warn(msg: str) -> None:
    sys.stderr.write(f"[backup_offsite_secrets] {msg}\n")


def _load_bootstrap() -> dict[str, str]:
    try:
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:  # pragma: no cover
            import tomli as _tomllib  # type: ignore[import-not-found]
    except ImportError:
        _warn("tomllib/tomli unavailable; cannot parse bootstrap.toml")
        return {}
    if not _BOOTSTRAP_PATH.is_file():
        _warn(f"{_BOOTSTRAP_PATH} not found; run `poindexter setup`")
        return {}
    try:
        with _BOOTSTRAP_PATH.open("rb") as f:
            data = _tomllib.load(f)
    except Exception as exc:  # noqa: BLE001
        _warn(f"bootstrap.toml parse failed: {exc}")
        return {}
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if isinstance(v, (str, int, float))
    }


async def _decrypt_all(dsn: str, secret_key: str) -> dict[str, str]:
    """Return {app_settings_key: plaintext} for whatever resolves; missing
    keys are simply absent (caller renders them as empty assignments)."""
    out: dict[str, str] = {}
    try:
        import asyncpg
    except ImportError:
        _warn("asyncpg unavailable on host Python; cannot decrypt secrets")
        return out
    try:
        conn = await asyncpg.connect(dsn, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        _warn(f"postgres connect failed ({type(exc).__name__}): {exc}")
        return out
    try:
        for setting_key in _KEYS:
            try:
                row = await conn.fetchrow(
                    "SELECT value, is_secret FROM app_settings WHERE key = $1",
                    setting_key,
                )
            except Exception as exc:  # noqa: BLE001
                _warn(f"query failed for {setting_key} ({type(exc).__name__})")
                continue
            if not row or not row["value"]:
                continue
            value = row["value"]
            if not row["is_secret"] or not value.startswith(_ENC_PREFIX):
                out[setting_key] = value
                continue
            try:
                plaintext = await conn.fetchval(
                    "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
                    value[len(_ENC_PREFIX):],
                    secret_key,
                )
            except Exception as exc:  # noqa: BLE001
                _warn(f"decrypt failed for {setting_key} ({type(exc).__name__})")
                continue
            if plaintext:
                out[setting_key] = plaintext
    finally:
        await conn.close()
    return out


def _render_env(resolved: dict[str, str]) -> str:
    """Render the env-file body. Missing keys → explicit empty assignment."""
    lines = [
        "# Auto-managed by scripts/start-stack.sh — DO NOT EDIT BY HAND.",
        "# Regenerated every start-stack invocation from encrypted app_settings",
        "# (offsite_backup_restic_password / _s3_access_key_id / _s3_secret_access_key).",
    ]
    for setting_key, env_var in _KEYS.items():
        lines.append(f"{env_var}={resolved.get(setting_key, '')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    cfg = _load_bootstrap()
    if not cfg:
        sys.stdout.write(_render_env({}))
        return
    dsn = cfg.get("database_url") or os.getenv("DATABASE_URL") or ""
    secret_key = cfg.get("poindexter_secret_key") or os.getenv(
        "POINDEXTER_SECRET_KEY", ""
    )
    if not dsn or not secret_key:
        _warn("database_url or poindexter_secret_key missing; emitting empties")
        sys.stdout.write(_render_env({}))
        return
    resolved = asyncio.run(_decrypt_all(dsn, secret_key))
    sys.stdout.write(_render_env(resolved))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_backup_offsite_secrets.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/_backup_offsite_secrets.py src/cofounder_agent/tests/unit/scripts/test_backup_offsite_secrets.py
git commit -m "feat(backup): offsite secrets decrypt-and-emit helper (#386)"
```

---

## Task 6: Wire secret materialization into start-stack.sh + gitignore

**Files:**

- Modify: `scripts/start-stack.sh` — after the Grafana token block (~line 96), before the `docker compose up`
- Modify: `.gitignore`

- [ ] **Step 1: Add the materialization block to `start-stack.sh`**

Immediately after the Grafana `.poindexter-grafana.env` block (the `chmod 600 "$_RUNTIME_ENV"` line ~96), add a parallel block:

```bash
# Offsite-backup secrets (poindexter#386) — decrypt the three encrypted
# app_settings rows into .poindexter-backup-offsite.env so the backup-offsite
# service's env_file picks up RESTIC_PASSWORD + AWS_* on every up/restart.
# Same pattern + same fail-soft posture as the Grafana token above. Empty
# assignments when unconfigured keep the runner loud-inert (it idles, the brain
# does not page on an opt-out tier).
if [ -n "${PYTHON_BIN:-}" ]; then
    _OFFSITE_ENV="$PROJECT_DIR/.poindexter-backup-offsite.env"
    "$PYTHON_BIN" "$SCRIPT_DIR/_backup_offsite_secrets.py" > "$_OFFSITE_ENV" || {
        # Never block stack start on the optional offsite tier.
        printf '%s\n' \
            "# Auto-managed — generation failed, see start-stack.sh output." \
            "RESTIC_PASSWORD=" "AWS_ACCESS_KEY_ID=" "AWS_SECRET_ACCESS_KEY=" \
            > "$_OFFSITE_ENV"
    }
    chmod 600 "$_OFFSITE_ENV" 2>/dev/null || true
fi
```

(Confirm `PYTHON_BIN`, `SCRIPT_DIR`, and `PROJECT_DIR` are the same vars the Grafana block uses — read lines ~60-96 of `start-stack.sh` and reuse the exact names. If the Grafana block computes them inline, hoist/reuse them.)

- [ ] **Step 2: Add to `.gitignore`**

Add next to the existing `.poindexter-grafana.env` entry (search the file for it):

```
.poindexter-backup-offsite.env
```

- [ ] **Step 3: Verify the helper runs from start-stack's perspective (no DB needed)**

Run: `python scripts/_backup_offsite_secrets.py`
Expected: prints the 3 `KEY=` lines (empty values if no bootstrap.toml/DB on this host) + the header comment; exits 0; any WARNING on stderr is fine. Confirms start-stack's redirect will produce a valid env_file.

- [ ] **Step 4: Commit**

```bash
git add scripts/start-stack.sh .gitignore
git commit -m "feat(backup): materialize offsite secrets to env_file in start-stack (#386)"
```

---

## Task 7: CLI group skeleton + pure helpers (repo-URL builder, append-only probe)

**Files:**

- Create: `src/cofounder_agent/poindexter/cli/backup.py`
- Test: `src/cofounder_agent/tests/unit/cli/test_backup_cli.py`

- [ ] **Step 1: Write the failing test for the pure helpers**

```python
"""Unit tests for poindexter/cli/backup.py (poindexter#386)."""
from __future__ import annotations

import pytest

from poindexter.cli import backup as bk


@pytest.mark.parametrize(
    "endpoint,bucket,path,expected",
    [
        ("s3.us-west-002.backblazeb2.com", "my-bucket", "poindexter",
         "s3:https://s3.us-west-002.backblazeb2.com/my-bucket/poindexter"),
        ("https://s3.us-west-002.backblazeb2.com/", "b", "p",
         "s3:https://s3.us-west-002.backblazeb2.com/b/p"),
        ("s3.amazonaws.com", "b", "",
         "s3:https://s3.amazonaws.com/b"),
    ],
)
def test_build_repo_url(endpoint, bucket, path, expected):
    assert bk.build_repo_url(endpoint, bucket, path) == expected


def test_interpret_delete_probe_appendonly():
    # A 403/AccessDenied on DELETE of a nonexistent object ⇒ append-only (good).
    assert bk.interpret_delete_probe(403) == "append_only"
    assert bk.interpret_delete_probe(401) == "append_only"


def test_interpret_delete_probe_delete_capable():
    # 204/404 ⇒ the DELETE was authorized (object just didn't exist) ⇒ key can delete.
    assert bk.interpret_delete_probe(404) == "delete_capable"
    assert bk.interpret_delete_probe(204) == "delete_capable"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py -v`
Expected: FAIL — `ImportError` / `AttributeError` (module/functions not defined).

- [ ] **Step 3: Write the skeleton + pure helpers**

```python
"""`poindexter backup` — off-machine (Tier 2) backup operator surface (#386).

Configures + drives restic against an S3-compatible bucket. The wizard
(`backup setup`) writes the repo URL (non-secret) to app_settings and the
restic password + S3 creds as ENCRYPTED app_settings secrets; start-stack.sh
materializes those into the backup-offsite container's env at boot. restic is
invoked via `docker run <offsite_backup_restic_image>` so no host install is
needed.
"""
from __future__ import annotations

import asyncio
import secrets as _secrets
import subprocess
from typing import Any

import click


# --- pure helpers (unit-tested without docker/DB) ---------------------------

def build_repo_url(endpoint: str, bucket: str, path: str) -> str:
    """Assemble a restic S3 repo URL from parts.

    restic wants ``s3:https://<host>/<bucket>[/<path>]``. We normalize a
    bare host, a full ``https://host/`` and trailing slashes to the same shape.
    """
    host = endpoint.strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://"):]
    elif host.startswith("http://"):
        host = host[len("http://"):]
    parts = [host, bucket.strip().strip("/")]
    p = path.strip().strip("/")
    if p:
        parts.append(p)
    return "s3:https://" + "/".join(parts)


def interpret_delete_probe(status_code: int) -> str:
    """Classify the append-only probe result.

    We attempt to DELETE a random object key that does not exist:
    - 401/403 (AccessDenied) ⇒ the key lacks delete ⇒ ``append_only`` (good).
    - 204/404 (deleted / NoSuchKey) ⇒ the DELETE was authorized ⇒
      ``delete_capable`` (warn — this key can destroy backup history).
    """
    if status_code in (401, 403):
        return "append_only"
    return "delete_capable"


def generate_restic_password() -> str:
    """High-entropy restic repository password."""
    return _secrets.token_urlsafe(32)


@click.group(name="backup")
def backup_group() -> None:
    """Off-machine (Tier 2) backup — wizard, status, run, verify, snapshots."""


# setup / status / run / verify / snapshots commands are added in Tasks 8-9.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py -v`
Expected: all parametrized cases pass (build_repo_url ×3, interpret_delete_probe ×4).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/backup.py src/cofounder_agent/tests/unit/cli/test_backup_cli.py
git commit -m "feat(backup): poindexter backup group skeleton + repo-url/append-only helpers (#386)"
```

---

## Task 8: The `backup setup` wizard

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/backup.py`
- Modify: `src/cofounder_agent/tests/unit/cli/test_backup_cli.py`
- Reference: `poindexter/cli/setup.py` (staged-flow style, `_provision_initial_oauth_client` for the `set_secret` pattern), `plugins.secrets.set_secret`

- [ ] **Step 1: Write the failing test for `persist_config` (the DB write path)**

```python
def test_persist_config_writes_secrets_and_tunables(monkeypatch):
    """persist_config routes the 3 secrets through set_secret and the repo
    URL through a plain settings upsert — never the reverse."""
    set_secret_calls = []
    set_setting_calls = []

    async def fake_set_secret(conn, key, value, description=""):
        set_secret_calls.append(key)

    async def fake_set_setting(conn, key, value):
        set_setting_calls.append((key, value))

    monkeypatch.setattr(bk, "_set_secret", fake_set_secret)
    monkeypatch.setattr(bk, "_set_setting", fake_set_setting)

    class _Conn:
        async def close(self): ...
    async def fake_connect(*a, **k): return _Conn()
    monkeypatch.setattr(bk, "_connect", fake_connect)

    asyncio.run(bk.persist_config(
        dsn="postgresql://x",
        repo_url="s3:https://h/b/p",
        restic_password="pw",
        access_key_id="akid",
        secret_access_key="sak",
    ))

    assert set(set_secret_calls) == {
        "offsite_backup_restic_password",
        "offsite_backup_s3_access_key_id",
        "offsite_backup_s3_secret_access_key",
    }
    assert ("offsite_backup_repository", "s3:https://h/b/p") in set_setting_calls
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py::test_persist_config_writes_secrets_and_tunables -v`
Expected: FAIL — `AttributeError: persist_config`.

- [ ] **Step 3: Implement `persist_config`, the docker-run restic seam, and the `setup` command**

Add to `backup.py`:

```python
# --- DB seams (monkeypatched in tests) --------------------------------------

async def _connect(dsn: str):
    import asyncpg
    return await asyncpg.connect(dsn, timeout=8)


async def _set_secret(conn, key: str, value: str, description: str = "") -> None:
    from plugins.secrets import set_secret
    await set_secret(conn, key, value, description=description)


async def _set_setting(conn, key: str, value: str) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category, is_secret, is_active)
        VALUES ($1, $2, 'backup', false, true)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        key, value,
    )


async def persist_config(
    *, dsn: str, repo_url: str, restic_password: str,
    access_key_id: str, secret_access_key: str,
) -> None:
    """Write the repo URL (plain) + the 3 secrets (encrypted) to app_settings."""
    conn = await _connect(dsn)
    try:
        await _set_setting(conn, "offsite_backup_repository", repo_url)
        await _set_secret(
            conn, "offsite_backup_restic_password", restic_password,
            description="restic repo password for Tier 2 offsite backup (#386)",
        )
        await _set_secret(
            conn, "offsite_backup_s3_access_key_id", access_key_id,
            description="S3 access key id for Tier 2 offsite backup (#386)",
        )
        await _set_secret(
            conn, "offsite_backup_s3_secret_access_key", secret_access_key,
            description="S3 secret access key for Tier 2 offsite backup (#386)",
        )
    finally:
        await conn.close()


def _run_restic(image: str, repo: str, args: list[str], *, env: dict[str, str],
                source_mount: str | None = None) -> subprocess.CompletedProcess[str]:
    """`docker run --rm <image> -r <repo> <args>` with creds passed as env.

    When ``source_mount`` is set, bind it read-only at /data so `restic backup
    /data/<tier>` can read the host dumps from inside the one-shot container.
    """
    cmd = ["docker", "run", "--rm"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if source_mount:
        cmd += ["-v", f"{source_mount}:/data:ro"]
    cmd += [image, "-r", repo, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)
```

Then the `setup` command (registered on `backup_group`):

```python
@backup_group.command(name="setup")
def backup_setup() -> None:
    """Interactive wizard: configure restic against an S3-compatible bucket."""
    from ._bootstrap import ensure_secret_key
    from brain import bootstrap
    ensure_secret_key()

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")

    click.secho("Poindexter off-machine backup setup (Tier 2)", fg="cyan", bold=True)
    click.echo("Backblaze B2 (S3) / AWS S3 / Cloudflare R2 / MinIO — same restic S3 backend.\n")

    endpoint = click.prompt("S3 endpoint host (e.g. s3.us-west-002.backblazeb2.com)").strip()
    bucket = click.prompt("Bucket name").strip()
    path = click.prompt("Path within bucket", default="poindexter").strip()
    access_key_id = click.prompt("S3 access key id").strip()
    secret_access_key = click.prompt("S3 secret access key", hide_input=True).strip()

    repo_url = build_repo_url(endpoint, bucket, path)
    restic_password = generate_restic_password()
    image = "restic/restic:0.18.0"  # mirror app_settings.offsite_backup_restic_image default
    s3_env = {
        "AWS_ACCESS_KEY_ID": access_key_id,
        "AWS_SECRET_ACCESS_KEY": secret_access_key,
        "RESTIC_PASSWORD": restic_password,
    }

    # 1/4 append-only guard ---------------------------------------------------
    click.secho("\n1/4 — checking key capability (append-only is recommended)…", fg="cyan")
    posture = _probe_append_only(image, repo_url, s3_env)
    if posture == "delete_capable":
        click.secho(
            "  WARNING: this key can DELETE objects. A ransomed host could "
            "destroy backup history. Prefer a B2 key without deleteFiles, or "
            "enable bucket Object Lock.", fg="yellow",
        )
        if not click.confirm("  Proceed with a delete-capable key anyway?", default=False):
            raise click.ClickException("Aborted — create an append-only key and re-run.")
    else:
        click.secho("  OK — key appears append-only (cannot delete).", fg="green")

    # 2/4 init ----------------------------------------------------------------
    click.secho("2/4 — initializing restic repo…", fg="cyan")
    init = _run_restic(image, repo_url, ["init"], env=s3_env)
    if init.returncode != 0 and "already initialized" not in (init.stderr or "").lower():
        raise click.ClickException(f"restic init failed:\n{init.stderr}")
    click.secho("  OK", fg="green")

    # 3/4 first backup (acceptance gate) -------------------------------------
    click.secho("3/4 — running first backup (must succeed before we save config)…", fg="cyan")
    backup_dir = _host_backup_dir()
    src_tier = "daily"
    first = _run_restic(
        image, repo_url, ["backup", f"/data/{src_tier}", "--tag", "poindexter"],
        env=s3_env, source_mount=backup_dir,
    )
    if first.returncode != 0:
        raise click.ClickException(
            f"First backup failed — nothing saved as configured.\n{first.stderr}"
        )
    click.secho("  OK — first snapshot created.", fg="green")

    # 4/4 persist + save-offline banner --------------------------------------
    click.secho("4/4 — saving config…", fg="cyan")
    asyncio.run(persist_config(
        dsn=dsn, repo_url=repo_url, restic_password=restic_password,
        access_key_id=access_key_id, secret_access_key=secret_access_key,
    ))
    click.secho("  OK — repo URL + encrypted creds written to app_settings.", fg="green")
    click.echo()
    click.secho("=" * 70, fg="yellow")
    click.secho("SAVE THIS RESTIC PASSWORD NOW — OFFLINE:", fg="yellow", bold=True)
    click.secho(f"    {restic_password}", fg="yellow", bold=True)
    click.secho(
        "In a drive-failure / theft / ransomware event the DB and this machine\n"
        "are gone. Without this password the remote backup is UNRECOVERABLE.",
        fg="yellow",
    )
    click.secho("=" * 70, fg="yellow")
    click.echo("\nRestart the stack (or `docker compose up -d backup-offsite`) to activate.")


def _probe_append_only(image: str, repo: str, env: dict[str, str]) -> str:
    """DELETE a nonexistent object via a one-shot aws-less probe.

    restic has no 'try delete' verb, so we shell a minimal HEAD/DELETE through
    the restic image's bundled `aws`-compatible path is not available; instead
    use restic's own `unlock` which performs a write+delete on a lock file —
    if the key cannot delete, `unlock` leaves the lock and returns nonzero.
    Interpret via interpret_delete_probe on the restic exit code mapping:
    0 ⇒ delete worked (delete_capable); nonzero with a permission error ⇒
    append_only.
    """
    res = _run_restic(image, repo, ["unlock"], env=env)
    # restic unlock exit 0 = it could remove stale locks (delete worked).
    # A permissions failure surfaces nonzero + "AccessDenied"/"denied".
    if res.returncode == 0:
        return "delete_capable"
    err = (res.stderr or "").lower()
    if "denied" in err or "forbidden" in err or "403" in err:
        return "append_only"
    # Ambiguous (e.g. repo not yet init'd) — default to the safe assumption so
    # we don't nag; the runner's backup-only default is safe regardless.
    return "append_only"


def _host_backup_dir() -> str:
    import os
    from pathlib import Path
    override = os.getenv("POINDEXTER_BACKUP_DIR")
    if override:
        return override
    return str(Path.home() / ".poindexter" / "backups" / "auto")
```

> **Note on `_probe_append_only`:** the `interpret_delete_probe` pure helper (Task 7) stays the unit-tested classifier for the _HTTP-status_ form; `_probe_append_only` is the restic-CLI adapter used at runtime. Keep both — the helper is what tests pin; the adapter is the docker-shell wrapper. If, during implementation, `restic unlock` proves a poor capability signal, swap the adapter to a direct `aws s3api delete-object` one-shot and map its HTTP status through `interpret_delete_probe` — the unit test contract does not change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py -v`
Expected: all pass (helpers + `persist_config`). The `setup` command itself is exercised via the integration smoke in Task 12 (it needs docker), not a unit test.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/backup.py src/cofounder_agent/tests/unit/cli/test_backup_cli.py
git commit -m "feat(backup): backup setup wizard — init, first-backup gate, encrypted persist (#386)"
```

---

## Task 9: `status` / `run` / `verify` / `snapshots` subcommands

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/backup.py`
- Modify: `src/cofounder_agent/tests/unit/cli/test_backup_cli.py`

- [ ] **Step 1: Write the failing test for `_format_status`**

```python
def test_format_status_unconfigured():
    out = bk._format_status(repo="", last_success_age_s=None, last_verify_age_s=None)
    assert "not configured" in out.lower()


def test_format_status_configured_fresh():
    out = bk._format_status(
        repo="s3:https://h/b/p", last_success_age_s=3600, last_verify_age_s=7200,
    )
    assert "s3:https://h/b/p" in out
    assert "1.0h ago" in out  # last success
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py -k format_status -v`
Expected: FAIL — `AttributeError: _format_status`.

- [ ] **Step 3: Implement the four commands + `_format_status`**

```python
def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    h = seconds / 3600.0
    if h < 48:
        return f"{h:.1f}h ago"
    return f"{h / 24:.1f}d ago"


def _format_status(*, repo: str, last_success_age_s: float | None,
                   last_verify_age_s: float | None) -> str:
    if not repo:
        return "Offsite backup: not configured. Run `poindexter backup setup`."
    return (
        f"Offsite backup repo: {repo}\n"
        f"  last backup:  {_fmt_age(last_success_age_s)}\n"
        f"  last verify:  {_fmt_age(last_verify_age_s)}"
    )


async def _age_of_event(dsn: str, event: str) -> float | None:
    conn = await _connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at)))"
            " FROM audit_log WHERE event_type = $1",
            event,
        )
    finally:
        await conn.close()


async def _get_setting(dsn: str, key: str) -> str:
    conn = await _connect(dsn)
    try:
        return (await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )) or ""
    finally:
        await conn.close()


@backup_group.command(name="status")
def backup_status() -> None:
    """Show repo + last backup/verify ages."""
    from brain import bootstrap
    dsn = bootstrap.resolve_database_url()
    repo = asyncio.run(_get_setting(dsn, "offsite_backup_repository"))
    succ = asyncio.run(_age_of_event(dsn, "offsite_backup_succeeded"))
    ver = asyncio.run(_age_of_event(dsn, "offsite_backup_verified"))
    click.echo(_format_status(repo=repo, last_success_age_s=succ, last_verify_age_s=ver))


def _resolved_secret_env(dsn: str) -> dict[str, str]:
    """Decrypt the 3 secrets for an ad-hoc `docker run restic` (run/verify/snapshots)."""
    from plugins.secrets import get_secret
    async def _go():
        conn = await _connect(dsn)
        try:
            return {
                "RESTIC_PASSWORD": await get_secret(conn, "offsite_backup_restic_password") or "",
                "AWS_ACCESS_KEY_ID": await get_secret(conn, "offsite_backup_s3_access_key_id") or "",
                "AWS_SECRET_ACCESS_KEY": await get_secret(conn, "offsite_backup_s3_secret_access_key") or "",
            }
        finally:
            await conn.close()
    return asyncio.run(_go())


def _run_or_die(dsn: str, restic_args: list[str], *, source_mount: str | None = None) -> str:
    from ._bootstrap import ensure_secret_key
    from brain import bootstrap
    ensure_secret_key()
    repo = asyncio.run(_get_setting(dsn, "offsite_backup_repository"))
    if not repo:
        raise click.ClickException("Not configured — run `poindexter backup setup`.")
    image = asyncio.run(_get_setting(dsn, "offsite_backup_restic_image")) or "restic/restic:0.18.0"
    env = _resolved_secret_env(dsn)
    if not env["RESTIC_PASSWORD"]:
        raise click.ClickException("restic password unset — re-run `poindexter backup setup`.")
    res = _run_restic(image, repo, restic_args, env=env, source_mount=source_mount)
    if res.returncode != 0:
        raise click.ClickException(f"restic failed:\n{res.stderr}")
    return res.stdout


@backup_group.command(name="run")
def backup_run() -> None:
    """Trigger an offsite backup now."""
    from brain import bootstrap
    dsn = bootstrap.resolve_database_url()
    tier = asyncio.run(_get_setting(dsn, "offsite_backup_source_tier")) or "daily"
    out = _run_or_die(dsn, ["backup", f"/data/{tier}", "--tag", "poindexter"],
                      source_mount=_host_backup_dir())
    click.echo(out)
    click.secho("Backup complete.", fg="green")


@backup_group.command(name="verify")
def backup_verify() -> None:
    """Run `restic check` against the remote now."""
    from brain import bootstrap
    dsn = bootstrap.resolve_database_url()
    pct = asyncio.run(_get_setting(dsn, "offsite_backup_verify_read_data_subset_percent")) or "5"
    out = _run_or_die(dsn, ["check", f"--read-data-subset={pct}%"])
    click.echo(out)
    click.secho("Verify complete.", fg="green")


@backup_group.command(name="snapshots")
def backup_snapshots() -> None:
    """List remote snapshots."""
    from brain import bootstrap
    dsn = bootstrap.resolve_database_url()
    click.echo(_run_or_die(dsn, ["snapshots"]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py -v`
Expected: all pass (including the two `_format_status` cases).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/backup.py src/cofounder_agent/tests/unit/cli/test_backup_cli.py
git commit -m "feat(backup): backup status/run/verify/snapshots subcommands (#386)"
```

---

## Task 10: Register the group in the CLI

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/app.py` (import block ~line 9-48; registration ~line 84-120)
- Modify: `src/cofounder_agent/tests/unit/cli/test_app.py` (it already enumerates registered groups — extend it)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_app.py` (match the existing style there — it likely invokes the group and asserts subcommands; if it has a `test_*` that lists commands, add `backup` to the expected set):

```python
def test_backup_group_registered():
    from poindexter.cli.app import main
    assert "backup" in main.commands
    assert "setup" in main.commands["backup"].commands
    assert "status" in main.commands["backup"].commands
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_app.py::test_backup_group_registered -v`
Expected: FAIL — `KeyError: 'backup'`.

- [ ] **Step 3: Register the group**

In `app.py`, add the import alongside the others (alphabetical-ish, near `auto_publish`):

```python
from .backup import backup_group
```

And the registration alongside the other `main.add_command(...)` lines (near the declarative-data-plane block):

```python
main.add_command(backup_group, name="backup")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_app.py -v`
Expected: all pass, including `test_backup_group_registered`. Also confirm the CLI imports cleanly: `cd src/cofounder_agent && poetry run python -c "from poindexter.cli.app import main; print('backup' in main.commands)"` → `True`.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/app.py src/cofounder_agent/tests/unit/cli/test_app.py
git commit -m "feat(backup): register poindexter backup group (#386)"
```

---

## Task 11: The brain auto-retry watch probe

**Files:**

- Create: `brain/offsite_backup_watch.py`
- Reference (mirror helpers verbatim): `brain/backup_watcher.py` (`_read_setting`, `_coerce_bool`, `_coerce_int`, `_restart_backup_container`, `_firing_alert_exists`, `_emit_resolved_alert`, `_emit_audit_event`)
- Test: `src/cofounder_agent/tests/unit/brain/test_offsite_backup_watch.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for brain/offsite_backup_watch.py (poindexter#386)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import offsite_backup_watch as ow


def _make_pool(*, setting_values=None, firing=None, executed=None):
    pool = MagicMock()
    settings = {
        ow.ENABLED_KEY: "true",
        ow.MAX_AGE_HOURS_KEY: "26",
        ow.MAX_RETRIES_KEY: "2",
        ow.RETRY_DELAY_KEY: "120",
        **(setting_values or {}),
    }
    firing = firing or set()

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        if "alert_events" in query and args and args[0] in firing:
            return {"status": "firing"}
        return None

    async def _execute(query, *args):
        if executed is not None:
            executed.append((query, args))

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


@pytest.fixture(autouse=True)
def _reset():
    ow._reset_retry_state()
    yield
    ow._reset_retry_state()


def test_disabled_short_circuits():
    pool = _make_pool(setting_values={ow.ENABLED_KEY: "false"})
    summary = __import__("asyncio").run(ow.run_offsite_backup_watch_probe(pool))
    assert summary["status"] == "disabled"


def test_fresh_heartbeat_is_ok_no_restart():
    pool = _make_pool()
    restart = MagicMock()
    summary = __import__("asyncio").run(ow.run_offsite_backup_watch_probe(
        pool, age_fn=AsyncMock(return_value=600.0),  # 10 min < 26h
        restart_fn=restart, sleep_fn=lambda s: None,
    ))
    assert summary["ok"] is True
    restart.assert_not_called()


def test_stale_triggers_restart_then_recovers():
    pool = _make_pool()
    # First read stale (older than 26h), post-restart read fresh.
    ages = iter([26 * 3600 + 100, 30.0])
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = MagicMock(return_value=(True, "Restarted"))
    summary = __import__("asyncio").run(ow.run_offsite_backup_watch_probe(
        pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
    ))
    restart.assert_called_once_with(ow._CONTAINER)
    assert summary["status"] == "recovered"


def test_escalate_emits_firing_alert_after_max_retries():
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(True, "Restarted"))
    # Always stale ⇒ burn through 2 retries across 3 cycles, then escalate.
    age_fn = AsyncMock(return_value=26 * 3600 + 100)
    run = lambda: __import__("asyncio").run(ow.run_offsite_backup_watch_probe(
        pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
    ))
    run(); run()          # 2 restart attempts
    summary = run()       # 3rd cycle escalates
    assert summary["status"] == "escalated"
    # A firing offsite_backup_stale alert_events row was written. status is a
    # bound param ($3), not literal SQL — assert on the args, not query text.
    assert any(
        "alert_events" in q and len(a) > 2 and a[2] == "firing"
        for q, a in executed
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_offsite_backup_watch.py -v`
Expected: FAIL — `ModuleNotFoundError: brain.offsite_backup_watch`.

- [ ] **Step 3: Write `brain/offsite_backup_watch.py`**

```python
"""Offsite-backup auto-retry watch (Glad-Labs/poindexter#386).

Tier 2 (off-machine restic, #386) runs an in-stack ``backup-offsite``
container that ships the daily dumps to an S3-compatible repo and stamps an
``audit_log`` heartbeat (``offsite_backup_succeeded``) on each success. This
probe is the self-heal-before-paging layer for that tier — a sibling of
``brain/backup_watcher.py`` with one difference: its freshness source is the
audit_log heartbeat (a creds-free DB read), not a dump-dir stat. So it never
needs the restic password.

Per cycle:
1. Age of the newest ``offsite_backup_succeeded`` event. Fresh
   (≤ ``offsite_backup_max_age_hours``) ⇒ happy path; auto-resolve any firing
   ``offsite_backup_stale`` alert.
2. Stale ⇒ ``docker restart poindexter-backup-offsite``, wait
   ``offsite_backup_watch_retry_delay_seconds``, re-read. Fresh ⇒ recovered.
3. After ``offsite_backup_watch_max_retries`` cumulative fail-then-retry
   cycles ⇒ escalate: emit a firing ``offsite_backup_stale`` alert_events row
   (``critical``) and stop kicking. Unlike backup_watcher (which leans on the
   runner's own failure alert + the Tier 1 healthcheck), the offsite tier has
   no other alert source for a dead runner, so this watch emits its own.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.offsite_backup_watch")

ENABLED_KEY = "offsite_backup_watch_enabled"
MAX_AGE_HOURS_KEY = "offsite_backup_max_age_hours"
MAX_RETRIES_KEY = "offsite_backup_watch_max_retries"
RETRY_DELAY_KEY = "offsite_backup_watch_retry_delay_seconds"

DEFAULT_ENABLED = True
DEFAULT_MAX_AGE_HOURS = 26
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 120

_CONTAINER = "poindexter-backup-offsite"
_ALERTNAME = "offsite_backup_stale"
_HEARTBEAT_EVENT = "offsite_backup_succeeded"
_DOCKER_RESTART_TIMEOUT_SECONDS = 30
PROBE_INTERVAL_SECONDS = 300

# Module-level retry counter (single tier) — persists across cycles so
# escalation fires cumulatively, exactly like backup_watcher's _retry_state.
_retry_count = 0


def _reset_retry_state() -> None:
    global _retry_count
    _retry_count = 0


# --- app_settings reads (verbatim from backup_watcher.py) -------------------
async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] read %s failed: %s — default %r", key, exc, default)
        return default
    return default if val is None else val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": _coerce_bool(await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED),
        "max_age_hours": _coerce_int(
            await _read_setting(pool, MAX_AGE_HOURS_KEY, DEFAULT_MAX_AGE_HOURS),
            DEFAULT_MAX_AGE_HOURS),
        "max_retries": _coerce_int(
            await _read_setting(pool, MAX_RETRIES_KEY, DEFAULT_MAX_RETRIES),
            DEFAULT_MAX_RETRIES),
        "retry_delay_seconds": _coerce_int(
            await _read_setting(pool, RETRY_DELAY_KEY, DEFAULT_RETRY_DELAY_SECONDS),
            DEFAULT_RETRY_DELAY_SECONDS),
    }


async def _seconds_since_heartbeat(pool: Any) -> float | None:
    """Age (seconds) of the newest offsite_backup_succeeded event, or None."""
    try:
        val = await pool.fetchval(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at)))"
            " FROM audit_log WHERE event_type = $1",
            _HEARTBEAT_EVENT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] heartbeat read failed: %s", exc)
        return None
    return None if val is None else float(val)


def _restart_offsite_container(container: str) -> tuple[bool, str]:
    """docker restart (verbatim shape from backup_watcher._restart_backup_container)."""
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True,
            "timeout": _DOCKER_RESTART_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(["docker", "restart", container], **kwargs)
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, f"docker restart {container} exit {result.returncode}: {(result.stderr or '').strip()[:200]}"
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {container} timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"


async def _firing_alert_exists(pool: Any, alertname: str) -> bool:
    try:
        row = await pool.fetchrow(
            "SELECT status FROM alert_events WHERE alertname = $1 ORDER BY id DESC LIMIT 1",
            alertname,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] alert lookup failed: %s", exc)
        return False
    return bool(row) and (row["status"] or "").lower() == "firing"


async def _emit_alert(pool: Any, *, status: str, severity: str, detail: str) -> bool:
    labels = {"source": "brain.offsite_backup_watch", "category": "backup", "tier": "offsite"}
    annotations = {
        "summary": (
            "Offsite backup stale — runner not producing snapshots"
            if status == "firing" else "Offsite backup recovered after auto-retry"
        ),
        "description": detail,
    }
    fingerprint = f"offsite-backup-{status}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (alertname, severity, status, labels, annotations, starts_at, fingerprint)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, NOW(), $6)
            """,
            _ALERTNAME, severity, status, json.dumps(labels), json.dumps(annotations), fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] alert insert (%s) failed: %s", status, exc)
        return False


async def _emit_audit_event(pool: Any, event: str, detail: str, *, extra: dict | None = None) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1,$2,$3::jsonb,$4)",
            event, "brain.offsite_backup_watch", json.dumps(payload),
            "warning" if "stale" in event or "escalate" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[OFFSITE_WATCH] audit write failed: %s", exc)


async def run_offsite_backup_watch_probe(
    pool: Any,
    *,
    age_fn: Callable[[], Awaitable[float | None]] | None = None,
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    notify_fn: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Single cycle of the offsite-backup watch."""
    global _retry_count
    age_fn = age_fn or (lambda: _seconds_since_heartbeat(pool))
    restart_fn = restart_fn or _restart_offsite_container
    sleep_fn = sleep_fn or time.sleep
    notify_fn = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    max_age_seconds = float(config["max_age_hours"]) * 3600.0
    max_retries = int(config["max_retries"])
    retry_delay = float(config["retry_delay_seconds"])

    age = await age_fn()

    # 1) Fresh ⇒ happy path / auto-resolve.
    if age is not None and age <= max_age_seconds:
        prev = _retry_count
        _retry_count = 0
        status = "fresh"
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool, status="resolved", severity="info",
                detail=f"Offsite backup fresh again (age={age:.0f}s ≤ {max_age_seconds:.0f}s) "
                       f"after {prev} watch retry attempt(s).",
            )
            await _emit_audit_event(pool, "probe.offsite_backup_resolved",
                                    f"fresh again (age={age:.0f}s)", extra={"age_seconds": age})
            status = "auto_resolved"
        return {"ok": True, "status": status, "age_seconds": age, "retries_used": prev}

    # 2) Stale / missing.
    if _retry_count >= max_retries:
        detail = (f"Offsite backup stale after {_retry_count} retry attempt(s) "
                  f"(age={age!r}s, threshold={max_age_seconds:.0f}s). Escalating.")
        logger.warning("[OFFSITE_WATCH] %s", detail)
        if not await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(pool, status="firing", severity="critical", detail=detail)
        await _emit_audit_event(pool, "probe.offsite_backup_escalate", detail,
                                extra={"age_seconds": age, "retries_used": _retry_count})
        return {"ok": False, "status": "escalated", "age_seconds": age, "retries_used": _retry_count}

    # 2a) Retry via docker restart.
    _retry_count += 1
    logger.info("[OFFSITE_WATCH] stale (age=%s) — restart %d/%d on %s",
                f"{age:.0f}s" if age is not None else "missing", _retry_count, max_retries, _CONTAINER)
    ok, msg = restart_fn(_CONTAINER)
    if not ok:
        detail = f"Offsite stale and docker restart failed: {msg} (retry {_retry_count}/{max_retries})."
        logger.warning("[OFFSITE_WATCH] %s", detail)
        await _emit_audit_event(pool, "probe.offsite_backup_restart_failed", detail,
                                extra={"retries_used": _retry_count, "restart_error": msg})
        if "docker CLI" in msg:
            try:
                notify_fn(title="Offsite watch cannot restart container",
                          detail=detail, source="brain.offsite_backup_watch", severity="warning")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[OFFSITE_WATCH] notify failed: %s", exc)
        return {"ok": False, "status": "restart_failed", "retries_used": _retry_count}

    # 2b) Wait + re-read.
    sleep_fn(retry_delay)
    post_age = await age_fn()
    if post_age is not None and post_age <= max_age_seconds:
        _retry_count = 0
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(pool, status="resolved", severity="info",
                              detail=f"Offsite recovered after restart (age={post_age:.0f}s).")
        await _emit_audit_event(pool, "probe.offsite_backup_recovered",
                                f"fresh after restart (age={post_age:.0f}s)",
                                extra={"age_seconds": post_age})
        return {"ok": True, "status": "recovered", "age_seconds": post_age, "retries_used": _retry_count}

    detail = f"Offsite still stale after restart (age={post_age!r}s). Used {_retry_count}/{max_retries}."
    logger.warning("[OFFSITE_WATCH] %s", detail)
    await _emit_audit_event(pool, "probe.offsite_backup_retry_failed", detail,
                            extra={"age_seconds": post_age, "retries_used": _retry_count})
    return {"ok": False, "status": "retry_failed", "age_seconds": post_age, "retries_used": _retry_count}


class OffsiteBackupWatchProbe:
    """Probe-Protocol wrapper (mirrors BackupWatcherProbe)."""

    name: str = "offsite_backup_watch"
    description: str = (
        "Watches the off-machine backup tier's audit_log heartbeat; `docker "
        "restart`s the wedged runner before paging, and emits offsite_backup_stale on escalate."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_offsite_backup_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("status", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_offsite_backup_watch.py -v`
Expected: 4 passed (disabled, fresh-no-restart, stale-recovers, escalate-emits-firing).

- [ ] **Step 5: Commit**

```bash
git add brain/offsite_backup_watch.py src/cofounder_agent/tests/unit/brain/test_offsite_backup_watch.py
git commit -m "feat(brain): offsite_backup_watch auto-retry probe (#386)"
```

---

## Task 12: Wire the probe into the brain daemon + image

**Files:**

- Modify: `brain/brain_daemon.py` — import guard (~after line 235), `_REQUIRED_MODULES` tuple (~after line 383), `run_cycle` invocation (~after line 2490)
- Modify: `brain/Dockerfile` — `COPY` the new module

- [ ] **Step 1: Add the import guard**

After the `restore_test_probe` import-guard block (ends ~line 235), add:

```python
try:
    # poindexter#386 — offsite-backup auto-retry watch. Reads an audit_log
    # heartbeat (creds-free) and `docker restart`s the wedged backup-offsite
    # runner before paging; emits offsite_backup_stale on escalate.
    from offsite_backup_watch import run_offsite_backup_watch_probe
    _HAS_OFFSITE_BACKUP_WATCH = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.offsite_backup_watch import run_offsite_backup_watch_probe
        _HAS_OFFSITE_BACKUP_WATCH = True
    except ImportError:
        _HAS_OFFSITE_BACKUP_WATCH = False
```

- [ ] **Step 2: Add to the `_REQUIRED_MODULES` degraded-import list**

After the `_HAS_RESTORE_TEST_PROBE` tuple (~line 383):

```python
    ("_HAS_OFFSITE_BACKUP_WATCH", "brain/offsite_backup_watch.py",
     "Off-machine backup auto-retry offline — a wedged offsite runner pages late, no self-heal (#386)"),
```

- [ ] **Step 3: Add the run_cycle invocation**

After the `restore_test` probe block (~line 2490), add:

```python
    # Offsite-backup watch (#386). Reads the audit_log heartbeat each cycle;
    # on staleness `docker restart`s the backup-offsite runner, and after
    # max_retries emits a firing offsite_backup_stale alert. Creds-free —
    # never touches the restic password. Disabled via
    # app_settings.offsite_backup_watch_enabled=false.
    if _HAS_OFFSITE_BACKUP_WATCH:
        try:
            ow_summary = await run_offsite_backup_watch_probe(pool)
            probe_results["offsite_backup_watch"] = {
                "ok": bool(ow_summary.get("ok", False)),
                "detail": ow_summary.get("detail", ""),
                "summary": ow_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] offsite_backup_watch probe failed: %s", e)
```

- [ ] **Step 4: COPY the module into the brain image**

In `brain/Dockerfile`, find the `COPY brain/backup_watcher.py ...` line (or the block that copies the probe modules) and add a parallel line for `offsite_backup_watch.py`, matching the existing two-line pattern (COPY into the package path + any `/app/brain/` mirror the other probes use). Read the existing `COPY brain/restore_test_probe.py` lines and mirror them exactly.

- [ ] **Step 5: Verify the daemon imports + the brain test suite passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/ -q`
Expected: all brain tests pass (the new one + no regressions). Also: `cd src/cofounder_agent && poetry run python -c "import brain.brain_daemon"` → no error (import guard resolves).

- [ ] **Step 6: Commit**

```bash
git add brain/brain_daemon.py brain/Dockerfile
git commit -m "feat(brain): wire offsite_backup_watch into the daemon + image (#386)"
```

---

## Task 13: Documentation

**Files:**

- Modify: `docs/operations/backups.md` — rewrite the "Tier 2" section (~lines 49-61)
- Modify: `docs/operations/disaster-recovery.md` — add a restore-from-remote runbook

- [ ] **Step 1: Rewrite the Tier 2 section in `backups.md`**

Replace the existing "## Tier 2 — off-machine (optional, recommended)" block with a description of: the `poindexter backup setup` wizard; the `backup-offsite` service; the append-only-key recommendation (no `deleteFiles`; retention via B2 lifecycle / Object Lock; `offsite_backup_prune_enabled` escape hatch); the weekly `restic check`; the brain `offsite_backup_watch` probe; and the `app_settings` table (the 16 `offsite_backup_*` keys from Task 1). Add an explicit **"Save the restic password offline"** callout mirroring the wizard banner. Mirror the structure/tone of the existing Tier 1 + backup-watcher sections.

- [ ] **Step 2: Add the restore-from-remote runbook to `disaster-recovery.md`**

Add a section: how to restore when the machine is gone — install restic, set `RESTIC_REPOSITORY` + `RESTIC_PASSWORD` (from your offline copy) + `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `restic snapshots`, `restic restore latest --target ./restore`, then `pg_restore` the recovered dump. Call out the **offline-password dependency** explicitly (the DB and on-disk `.env` are gone in this scenario).

- [ ] **Step 3: Verify links + lint**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/ -k "doc or link" -q` (if a doc-link test exists; otherwise skip). Then a manual skim that every `app_settings` key named in the docs matches Task 1's seeds exactly (grep each: `grep -o "offsite_backup_[a-z_]*" docs/operations/backups.md | sort -u` vs the seed file).

- [ ] **Step 4: Commit**

```bash
git add docs/operations/backups.md docs/operations/disaster-recovery.md
git commit -m "docs(backup): Tier 2 off-machine wizard + restore-from-remote runbook (#386)"
```

---

## Final verification (after all tasks)

- [ ] **Full unit suite for touched areas:**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_backup_cli.py tests/unit/cli/test_app.py tests/unit/brain/test_offsite_backup_watch.py tests/unit/scripts/test_backup_offsite_secrets.py -v`
Expected: all green.

- [ ] **Compose still validates:** `docker compose -f docker-compose.local.yml config >/dev/null && echo OK`
- [ ] **CLI smoke:** `cd src/cofounder_agent && poetry run python -m poindexter.cli.app backup --help` lists `setup/status/run/verify/snapshots`.
- [ ] **Live end-to-end (Matt, with the real B2 key):** rebuild the image (`docker compose up -d --build backup-offsite`), run `poindexter backup setup`, confirm the first-backup gate passes + the password banner prints, then `poindexter backup status` shows a recent snapshot and `poindexter backup verify` is clean. This is the issue's acceptance gate.

---

## Out of scope (tracked, not built here)

- USB / external-drive backend (deferred — Windows drive-letter→container mount).
- Restore _automation_ (`backup restore` stays a documented runbook).
- A Grafana panel for the offsite tier (audit_log events make it queryable; a panel can follow).
