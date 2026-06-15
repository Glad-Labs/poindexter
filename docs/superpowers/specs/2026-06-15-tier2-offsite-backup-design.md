# Tier 2 — off-machine backup wizard (cloud-first, in-stack) — design

**Issue:** [Glad-Labs/poindexter#386](https://github.com/Glad-Labs/poindexter/issues/386)
**Date:** 2026-06-15
**Status:** Approved (cloud-first / in-stack slice; auto-retry watch folded in; USB deferred)
**Builds on:** Tier 1 in-stack dumps ([#385](https://github.com/Glad-Labs/poindexter/issues/385), shipped),
backup-watcher ([#388](https://github.com/Glad-Labs/poindexter/issues/388), shipped),
restore-test probe ([#441](https://github.com/Glad-Labs/poindexter/issues/441), shipped).

## Problem

Tier 1 ships hourly + daily `pg_dump` files to a host bind mount
(`~/.poindexter/backups/auto/{hourly,daily}/`). That survives `docker volume
prune`, Docker Desktop reinstalls, and accidental drops. It does **not** survive
the dominant risks on a self-hosted PC: **drive failure, theft, ransomware** —
all of which take the dumps with the machine. A 2026-06-09 audit underlined two
specifics (issue comment):

1. Every current tier shares one physical site, and the restic repo is fully
   host-writable — a ransomed host can encrypt/delete the backups along with the
   data they protect.
2. Nothing verifies the _remote_ copy's integrity; bit-rot would only surface at
   restore time.

This work ships the off-machine tier: `restic`-encrypted snapshots to an
S3-compatible cloud bucket, configured by an interactive wizard, with
append-only credentials and scheduled remote verification.

## Scope of this slice

restic is backend-agnostic — the same `restic backup` / `restic check` runs
against USB, S3, B2, R2, MinIO; only the repository URL + credentials differ.
So the runner, verifier, and wizard are built **once**. This slice ships the
**cloud path** (S3-compatible: Backblaze B2-S3, AWS S3, Cloudflare R2, MinIO —
identical restic S3 backend) and **defers USB**, which needs a Windows
drive-letter→container bind-mount solved separately. The architecture below is
backend-agnostic, so USB later is an additive wizard branch + a mount step.

> **Backend note.** The operator's bucket is **S3-compatible** (Backblaze
> created via the S3 API, not the native B2 API). restic's **S3 backend** is
> what's used (`s3:https://<endpoint>/<bucket>/<path>`, creds via
> `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`). Naming the settings
> `..._s3_access_key_id` / `..._s3_secret_access_key` (not `b2_*`) keeps the
> exact same path working for AWS / R2 / MinIO with no code change.

## What Tier 2 backs up

The Tier 1 **daily** dump directory (`<source_tier>`, default `daily`). Tier 2
is "ship Tier 1's dumps off-machine," which composes cleanly: Tier 1 owns
`pg_dump` + local rotation; Tier 2 owns encryption + transport + remote
retention. The daily tier (7 dumps) is the right disaster-recovery resolution;
the churning hourly tier (24 dumps) is local-only by design.

## Architecture

The work breaks into these parts — each independently testable and reusing an
existing pattern (A the surface, B–C the wizard + runner, D–E secrets +
verification, G the watch; F is the cross-cutting security posture).

### A. Operator surface — `poindexter backup` group

A new click group registered in `poindexter/cli/app.py`, matching the
declarative-operator-group precedent (`taps`, `retention`, `webhooks`,
`qa-gates`). Keeping it out of `poindexter setup` (the bootstrap-toml first-run
wizard) avoids overloading an unrelated concern.

| Command                       | Does                                                             |
| ----------------------------- | ---------------------------------------------------------------- |
| `poindexter backup setup`     | the interactive wizard (issue's `setup --backup-target`)         |
| `poindexter backup status`    | repo, last snapshot age, last verify result, append-only posture |
| `poindexter backup run`       | trigger an offsite backup now (one-shot `restic backup`)         |
| `poindexter backup verify`    | trigger `restic check` now                                       |
| `poindexter backup snapshots` | list remote snapshots (`restic snapshots --json`)                |

The issue's literal `poindexter setup --backup-target` is **not** added; if
wanted later it's a one-line alias that calls `backup setup`.

### B. Execution — `backup-offsite` compose service

Mirrors Tier 1 one-for-one. The existing comment placeholder in
`docker-compose.local.yml` (line ~1747, _"Tier 2 (off-machine: USB / S3 / B2 via
restic) is a separate concern…"_) is replaced by the real service.

- **Image.** Extend `scripts/Dockerfile.backup` to also carry the `restic`
  binary (it already has `postgresql-client` for the psql settings-reads). One
  image, two services (`poindexter-backup-{hourly,daily}` keep using it for
  pg_dump; `poindexter-backup-offsite` uses it for restic). Pin the restic
  version in the Dockerfile.
- **Entrypoint.** New `scripts/backup-offsite/run.sh`, structured like
  `scripts/backup/run.sh`: a self-contained loop that re-reads tunables from
  `app_settings` via psql each tick (cadence/repo/source-tier/enabled), runs the
  restic op, records success, and `emit_alert`s into `alert_events` on any
  non-zero exit (same schema → same brain dispatcher → same Telegram/Discord).
- **Per tick:** `restic backup <dump-dir>/<source_tier>`. **Backup-only** under
  the append-only default (see §F) — no `forget`/`prune`, both of which delete
  objects. On success, record a heartbeat (§E).
- **Verify (gated, not a separate loop):** each tick also checks whether
  `offsite_backup_verify_interval_hours` (default 168 / weekly) has elapsed since
  `_last_verify_at`; if due, run `restic check --read-data-subset=<percent>%`
  (default 5%) — read-only, append-only-safe. Time-gated via the DB heartbeat
  exactly like the restore-test probe's daily gate, so a runner restart doesn't
  re-trigger it early.
- **Inert when unconfigured.** Empty repository/creds → the loop logs
  _"offsite backup not configured — run `poindexter backup setup`"_ and idles
  (no error, no alert). The compose env interpolation uses `${VAR:-}` (empty
  default), **not** `${VAR:?}`, so the service ships dormant in the default stack
  and never blocks `docker compose up`. (Tier 1's `PGPASSWORD` uses `:?` because
  it's always required; the opt-in offsite tier must not.)
- **Source mount.** Read-only bind of `~/.poindexter/backups/auto` (the same
  host dir Tier 1 writes), so restic reads the daily dumps.

### C. The wizard (`poindexter backup setup`)

Staged + verified, in the style of `setup.py`'s `1/N…N/N` flow. Shells out to
the pinned `restic/restic` image via `docker run` — **no host restic install
required**, consistent with how the brain orchestrates throwaway containers.

1. **Detect.** Existing `offsite_backup_repository` configured? S3 creds already
   in env? `restic`/`docker` reachable? Report what's found.
2. **Prompt.** Backend label (Backblaze B2 (S3) default; AWS S3 / R2 / MinIO same
   path), S3 endpoint host, bucket, path, access-key-id; **secret access key via
   hidden input** (`click.prompt(hide_input=True)`). **Generate** a high-entropy
   restic repository password (`secrets.token_urlsafe`).
3. **Append-only guard.** Probe the key's capability: attempt a DELETE of a
   random non-existent object key. An append-only key returns `AccessDenied`
   (good); a full key returns `NoSuchKey` (warn — "this key can destroy backup
   history; create one without deleteFiles, or enable bucket Object Lock"). The
   probe leaves no litter (the target never existed). Non-fatal: the operator
   can proceed with a warning acknowledged.
4. **`restic init`.** `docker run … restic init` against the repo (idempotent —
   "already initialized" is treated as success).
5. **First backup — the acceptance gate.** Run `restic backup` of the daily dump
   dir and **confirm it completes before the wizard exits** (issue requirement).
   On failure, surface the restic stderr and exit non-zero; nothing is persisted
   as "configured" until a real snapshot exists.
6. **Persist + save-offline banner.** Write the three secrets as encrypted
   `app_settings` rows (§D) and the non-secret tunables; **print the restic
   password once** with a _"SAVE THIS NOW — in a drive-failure/ransomware event
   the DB and this machine are gone; without this password the remote backup is
   unrecoverable"_ banner (same pattern as the OAuth `client_secret` echo in
   `setup.py`). Tell the operator to re-run `start-stack.sh` (or
   `docker compose up -d backup-offsite`) to activate the loop.

### D. Secret storage — DB-first, materialized to a derived env file

Honors the "only `database_url` on disk" rule by reusing the codebase's
**"DB is source of truth, disk is a regenerated cache"** pattern — the same one
`grafana_webhook_oauth_jwt` already uses.

- **Durable copy:** three encrypted `app_settings` rows (`is_secret=true`,
  `enc:v1:` via `plugins.secrets.set_secret`):
  `offsite_backup_restic_password`, `offsite_backup_s3_access_key_id`,
  `offsite_backup_s3_secret_access_key`.
- **Materialization:** a new `scripts/_backup_offsite_secrets.py` helper
  (mirroring `scripts/_grafana_webhook_token.py`) reads + decrypts those rows and
  emits them as `RESTIC_PASSWORD` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
  assignments. `start-stack.sh` calls it and writes a **git-ignored, chmod-600
  `.poindexter-backup-offsite.env`** next to `docker-compose.local.yml`; the
  `backup-offsite` service loads it via `env_file:`. Regenerated every
  `start-stack.sh`; empty assignments on missing config (loud-inert, per
  `feedback_no_silent_defaults`).
- **On-disk delta: zero new persistent secrets.** The only durable on-disk
  secret remains the master key (`POINDEXTER_SECRET_KEY`) in `bootstrap.toml`,
  already present. `bootstrap.toml` gets nothing new.

`.gitignore` += `.poindexter-backup-offsite.env` (mirrors the existing
`.poindexter-grafana.env` ignore).

### E. Scheduled verification + freshness heartbeat

The verification the audit asked for runs **in the runner** (which already holds
the creds) — `restic check --read-data-subset` weekly — so no second component
needs the restic password, and the brain never touches it.

- **Verify outcome → DB + alerts.** On `check` failure (corruption/bit-rot),
  `emit_alert` (critical) → dispatcher → Telegram. On success, stamp the
  heartbeat.
- **Heartbeat = audit_log (creds-free, no new mount).** After each successful
  `restic backup` / `restic check`, the runner writes an `audit_log` event
  (`offsite_backup_succeeded` / `offsite_backup_verified`) via the psql access it
  already has. The brain watch (§G) reads that event's age — exactly how the
  restore-test probe reads its own audit_log marker — so there's no shared file,
  no writable mount, and no restic password in the brain. A derived
  `app_settings.offsite_backup_last_success_at` mirror is updated for the
  `backup status` CLI + a Grafana panel.

Dead/wedged-runner detection — the case `alert_events` can't catch, because a
dead runner emits nothing — is the brain auto-retry watch's job (§G), which
supersedes the Tier-1-style compose healthcheck (no writable marker file, no
DB-flap-sensitive container healthcheck; the container keeps
`restart: unless-stopped`).

### F. Append-only / ransomware posture (audit ask #1)

The secure default is a **write-only S3 key** (no `deleteFiles` / no
`s3:DeleteObject`):

- restic `backup` (write) ✅ and `check` (read) ✅ both work.
- restic `forget` and `prune` (both delete objects) **fail** — which is the
  point: a compromised host cannot destroy snapshot history.
- **Consequence — host-driven retention is off by design.** Retention moves to
  the bucket: B2 **lifecycle rules** (expire objects after N days) or **Object
  Lock / WORM** (the belt-and-suspenders upgrade). The wizard prints lifecycle
  guidance. At ~$0.005/GB/mo and ~daily 100–200 MB dumps, unbounded growth is
  cents/month for a long time, so "never reap" is a safe default until lifecycle
  is set.
- **Escape hatch.** `offsite_backup_prune_enabled` (default `false`). If an
  operator deliberately uses a _privileged_ key, flipping this lets the runner
  also `forget --keep-* … --prune`. Default off preserves the append-only
  guarantee; the knob exists so the secure default isn't a dead end.

### G. Brain auto-retry watch (self-heal before paging)

New standalone `brain/offsite_backup_watch.py`, structurally a sibling of
`brain/backup_watcher.py` — same freshness → `docker restart` →
recover-or-escalate shape, same `app_settings`-driven tunables, same injectable
seams (`read_age_fn` / `restart_fn` / `sleep_fn` / `notify_fn`), same
Probe-Protocol wrapper, wired into `brain_daemon.run_cycle` behind a
`_HAS_OFFSITE_BACKUP_WATCH` import guard + the degraded-import warning list. The
one difference from `backup_watcher`: its freshness source is the **audit_log
heartbeat** (§E), not a dump-dir stat — so it needs no restic creds, just the
brain's own pool.

Per cycle:

1. **Freshness.** Age of the newest `offsite_backup_succeeded` audit_log event.
   ≤ `offsite_backup_max_age_hours` (default 26h) → happy path; if an
   `offsite_backup_stale` alert is firing, write a `status='resolved'` row (the
   dispatcher's `[RESOLVED · …]` contract, same as `backup_watcher`).
2. **Stale → auto-retry.** `docker restart poindexter-backup-offsite`, sleep
   `offsite_backup_watch_retry_delay_seconds` (default 120), re-read the age. A
   fresh event ⇒ recovered (reset the per-tier counter, auto-resolve).
3. **Escalate.** After `offsite_backup_watch_max_retries` (default 2) cumulative
   fail-then-retry cycles, stop kicking and **emit a firing
   `offsite_backup_stale` alert** (`critical`) → dispatcher → Telegram, leaving
   it firing until recovered.

The one deliberate divergence from `backup_watcher`: this watch **emits its own
firing alert on escalate**, because the dead-runner case has no other alert
source (the runner emitted nothing, and there's no compose healthcheck here).
`backup_watcher` could lean on the runner's `pg_dump`-failure alert + the Tier 1
healthcheck; the offsite watch is self-contained.

### Failure taxonomy (the "no false pages" contract)

Same `operator_notifier` severity matrix the sibling probes use.

| Condition                                       | Severity                | Reaches                                                                               |
| ----------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| Backup + (when due) verify succeed              | `info`                  | audit / Discord                                                                       |
| **`restic check` fails** (corruption / bit-rot) | `critical`              | **Telegram** + Discord + log                                                          |
| **`restic backup` fails** (network, auth, disk) | `critical`              | **Telegram** + Discord + log                                                          |
| Runner wedged/dead (heartbeat stale)            | auto-retry → `critical` | brain watch (§G) `docker restart`s first; emits `offsite_backup_stale` if unrecovered |
| Append-only key probe shows delete-capable      | `warning` (wizard-time) | wizard stderr + Discord                                                               |
| Not configured                                  | —                       | inert, no page                                                                        |

Rationale matches Tier 1 / restore-test: a _corrupt or failing remote backup_ is
the real alarm (Telegram); benign infra is Discord-grade. The runner re-reads
`app_settings` each tick, so disabling (`offsite_backup_enabled=false`) silences
without a restart.

## app_settings keys

**Non-secret tunables** — seeded in `services/settings_defaults.py`
(`DEFAULTS`), applied every boot via `seed_all_defaults` (per
`feedback_seed_data_in_baseline_not_new_migrations` — no one-shot seed
migration):

| Key                                              | Default                | Notes                                                  |
| ------------------------------------------------ | ---------------------- | ------------------------------------------------------ |
| `offsite_backup_enabled`                         | `true`                 | Loop runs but is inert until creds present             |
| `offsite_backup_repository`                      | `` (empty)             | `s3:https://<endpoint>/<bucket>/<path>`; empty ⇒ inert |
| `offsite_backup_interval`                        | `24h`                  | `<N>{s\|m\|h\|d}`, parsed like Tier 1                  |
| `offsite_backup_source_tier`                     | `daily`                | Which Tier 1 dump dir to ship                          |
| `offsite_backup_restic_image`                    | `restic/restic:0.18.0` | Pinned; wizard + runner + verify share it              |
| `offsite_backup_max_age_hours`                   | `26`                   | Stale threshold for the brain watch (§G)               |
| `offsite_backup_verify_enabled`                  | `true`                 | Master switch for the weekly check                     |
| `offsite_backup_verify_interval_hours`           | `168`                  | Weekly                                                 |
| `offsite_backup_verify_read_data_subset_percent` | `5`                    | `restic check --read-data-subset` sample               |
| `offsite_backup_watch_enabled`                   | `true`                 | Master switch for the brain auto-retry watch (§G)      |
| `offsite_backup_watch_max_retries`               | `2`                    | Cumulative docker-restart attempts before escalating   |
| `offsite_backup_watch_retry_delay_seconds`       | `120`                  | Wait between restart and re-check                      |
| `offsite_backup_prune_enabled`                   | `false`                | Off = append-only-safe; on requires a privileged key   |
| `offsite_backup_keep_daily`                      | `7`                    | Only consulted when prune is enabled                   |
| `offsite_backup_keep_weekly`                     | `4`                    | "                                                      |
| `offsite_backup_keep_monthly`                    | `6`                    | "                                                      |

**Encrypted secrets** (`is_secret=true`, written by the wizard, never seeded):
`offsite_backup_restic_password`, `offsite_backup_s3_access_key_id`,
`offsite_backup_s3_secret_access_key`.

Container name (`poindexter-backup-offsite`) and the materialized env-file name
(`.poindexter-backup-offsite.env`) are constants, not settings — same rationale
as the sibling probes (changing them needs coordinated edits in compose +
start-stack).

## Companion changes (in scope)

- **`scripts/Dockerfile.backup`** — add the pinned `restic` binary.
- **`docker-compose.local.yml`** — new `backup-offsite` service (env_file +
  `${VAR:-}` interpolation + read-only backups mount + `restart: unless-stopped`,
  no healthcheck — the brain watch §G owns staleness); replace the stale Tier 2
  placeholder comment.
- **`scripts/start-stack.sh`** — call `_backup_offsite_secrets.py`, write
  `.poindexter-backup-offsite.env` (mirrors the Grafana block).
- **`scripts/_backup_offsite_secrets.py`** — new decrypt-and-emit helper.
- **`.gitignore`** — add `.poindexter-backup-offsite.env`.
- **`poindexter/cli/backup.py`** + registration in `cli/app.py`.
- **`services/settings_defaults.py`** — the non-secret defaults above.
- **`brain/offsite_backup_watch.py`** — new sibling probe (the auto-retry watch).
- **`brain/Dockerfile`** — `COPY` the new module + mirror into `/app/brain/` (the
  two-line brain-module pattern every brain probe follows).
- **`brain/brain_daemon.py`** — wire the probe into `run_cycle` behind
  `_HAS_OFFSITE_BACKUP_WATCH` (+ the degraded-import warning list), exactly like
  `backup_watcher` / `restore_test_probe`.
- **`docs/operations/backups.md`** — rewrite the Tier 2 section from "see Matt's
  scheduled task" to the real wizard + append-only + verify story.
- **`docs/operations/disaster-recovery.md`** — a restore-from-remote runbook
  (`restic restore`, the offline-password dependency).

## Testing (contract tests + docs are the default)

- **Wizard helpers** (`tests/unit/cli/test_backup_cli.py`): repo-URL builder
  (endpoint+bucket+path → `s3:…`), append-only capability probe (AccessDenied ⇒
  pass, NoSuchKey ⇒ warn), secret persistence routes through
  `plugins.secrets.set_secret`, first-backup-failure aborts without persisting.
  Every `docker run` is an injectable seam — no real restic/docker in unit tests.
- **`scripts/_backup_offsite_secrets.py`** (`tests/unit/scripts/…`): decrypts
  rows → correct `KEY=value` lines; empty/missing config → explicit empty
  assignments (loud-inert).
- **`brain/offsite_backup_watch.py`**
  (`tests/unit/brain/test_offsite_backup_watch.py`, mirroring
  `test_backup_watcher.py`): fresh heartbeat → happy / auto-resolve; stale →
  restart → recover; stale → restart fails or still stale → escalate emits a
  firing `offsite_backup_stale`; retry budget persists across cycles; disabled →
  skip. Every docker/DB op is an injectable seam — no real docker.
- **`run.sh`**: shellcheck (CI already lints shell) + a smoke that the
  not-configured path idles and the alert-on-failure path emits the right
  `alert_events` row shape (mirrors `scripts/backup/run.sh` coverage).
- **`docker-compose.local.yml`**: the existing compose-drift / lint checks cover
  the new service; assert the `${VAR:-}` (not `:?`) interpolation so a fresh
  clone still boots.

## Out of scope

- **USB / external-drive backend** — deferred (needs the Windows
  drive-letter→container mount). Additive wizard branch later; the restic
  runner/verify is already backend-agnostic.
- **Restore _automation_** — `poindexter backup restore` is documented as a
  runbook (`restic restore`), not wizard-automated; a bad restore is
  operator-judgment territory.
- **Media / R2 asset backup** — Tier 2 targets the Postgres dumps (the
  irreplaceable state). Object assets already live in R2 and are out of scope.

## Disaster-recovery caveat (called out, not papered over)

Even with DB-first secret storage, the restic password's **only** survivable
copy in a drive-failure / theft / ransomware event is the operator's **offline
copy** (the wizard's one-time printout / a password manager). In that scenario
the DB _and_ the on-disk `.poindexter-backup-offsite.env` are gone with the
machine; the remote repo is unrecoverable without the password. This is inherent
to encrypted backups and independent of where the day-to-day operational copy
lives — the DB is the source for routine reads, not the lifeboat.
