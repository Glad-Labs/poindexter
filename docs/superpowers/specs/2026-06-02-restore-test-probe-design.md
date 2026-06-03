# Restore-test probe — design

**Issue:** [Glad-Labs/poindexter#441](https://github.com/Glad-Labs/poindexter/issues/441)
**Date:** 2026-06-02
**Status:** Approved (brain-probe approach, full-depth verification)
**Surfaced by:** recommendation #10 of the 2026-05-08 self-healing + backups audit
(`.shared-context/audits/2026-05-08-self-healing-and-backups-audit.md`).

## Problem

Backups are _created_ and their freshness is _monitored_, but no layer ever
verifies a dump actually **restores**:

- `scripts/db-backup-local.sh` runs `pg_restore --list` — proves the dump's
  table-of-contents is _readable_, not that it restores into a live DB.
- `brain/backup_watcher.py` checks dump **freshness** (is a recent file
  present?) — not its **restorability**.
- Nothing does the real thing: `pg_restore` into a running Postgres + assert
  the data survived.

A corrupt-but-fresh dump is _worse_ than no dump: it gives false confidence.
This probe closes that gap.

## Why a brain probe (not CI)

The dumps live only on Matt's PC (`~/.poindexter/backups/auto/daily/`) and on
`F:\` via restic. GitHub Actions runners can reach neither. The brain already
has everything required and is the established home for periodic self-healing
checks:

- `/var/run/docker.sock` + the docker CLI (bind-mounted; used by
  `backup_watcher`, `migration_drift_probe`, `compose_drift_probe`).
- `~/.poindexter/backups` mounted read-only at `/host-backups` (added by the
  audit's fix #1).
- The `pgvector/pgvector:pg16` image already pulled (it's the prod Postgres
  image — required so a dump's `CREATE EXTENSION vector` restores).

**Result: zero compose changes.** Everything the probe needs is already wired.

## Architecture

New standalone module `brain/restore_test_probe.py`, mirroring the
`backup_watcher.py` / `smart_monitor.py` pattern exactly:

- stdlib + asyncpg only (no heavy deps — brain stays lockable in isolation).
- A top-level `run_restore_test_probe(pool, *, <injectable seams>)` entry point
  - a `RestoreTestProbe` `Probe`-Protocol wrapper for the registry.
- Every tunable is an `app_settings` row.
- Alerts via `notify_operator`; observability via `audit_log` events.
- Wired into `brain_daemon.run_cycle` behind a `_HAS_RESTORE_TEST_PROBE`
  import guard + the `_REQUIRED_MODULES` degraded-import warning list, exactly
  like the sibling probes.

### Flow (once per `restore_test_interval_hours`, default 24h)

1. **Daily gate.** Query `audit_log` for the newest
   `probe.restore_test_completed` / `probe.restore_test_failed` event. If
   younger than the interval → return `status="skipped"`. State lives in the DB
   (not module memory) so a brain restart doesn't re-trigger the heavy run. A
   module-level cache short-circuits the query on hot cycles.
2. **Pick dump.** Newest `poindexter_brain_*.dump` (fallback: any `*.dump`,
   skipping `.tmp`) by mtime under `<restore_test_backup_dir>/<tier>/`
   (default `/host-backups/auto/daily/`), read via the brain's own `:ro`
   mount. None found → `warning` (could be a fresh install).
3. **Spin throwaway.** `docker rm -f poindexter-restore-test` (idempotent),
   then `docker run -d --name poindexter-restore-test --network <net>
-e POSTGRES_PASSWORD=<random> -e POSTGRES_DB=poindexter_brain
pgvector/pgvector:pg16`. No published ports (internal only — no host port
   conflict). `<net>` is **discovered at runtime** from `poindexter-worker`'s
   networks (`docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}
{{$k}}{{"\n"}}{{end}}' poindexter-worker`), so there's no hardcoded compose
   project name.
4. **Load dump.** `docker cp <dump> poindexter-restore-test:/tmp/restore.dump`
   — streams from the brain's filesystem, sidestepping the "sibling-container
   bind mount needs a _host_ path, not my container path" trap.
5. **Restore.** Poll `pg_isready` (async), then
   `docker exec … pg_restore --no-owner --no-privileges -d poindexter_brain
/tmp/restore.dump`. Capture exit code + stderr (informational — see verdict
   policy; benign non-zero exits are expected with `--no-owner`).
6. **Migrations smoke.** `docker exec
-e DATABASE_URL=postgresql://postgres:<pw>@poindexter-restore-test:5432/poindexter_brain
-e POINDEXTER_BACKEND_ROOT=/app poindexter-worker
python /opt/scripts/ci/migrations_smoke.py`. The worker re-runs the
   production migration runner against the restored dump — proving the current
   migration set applies cleanly _on top of_ real prod data. Gated by
   `restore_test_run_migrations_smoke` (default `true`) as a safety valve.
7. **Row-count asserts.** `docker exec … psql -tAc "SELECT count(*) FROM <t>"`
   for each table in `restore_test_critical_tables`
   (default `posts,app_settings,audit_log`) ≥ `restore_test_min_row_count`
   (default 1), plus `schema_migrations` non-empty. Table names validated
   against `^[A-Za-z_][A-Za-z0-9_]*$` before interpolation (operator-supplied
   via settings).
8. **Verdict + teardown.** `_decide_verdict(...)` combines pg_restore exit,
   smoke result, and row counts into pass/fail/severity. Write the terminal
   `audit_log` event, page on failure, and **always** `docker rm -f` the
   throwaway in a `finally`.

### Companion changes (in scope)

- **`scripts/ci/migrations_smoke.py`** — honor a `POINDEXTER_BACKEND_ROOT`
  env override for `BACKEND_ROOT`/`MIGRATIONS_DIR`, falling back to the current
  `Path(__file__).parents[2]` logic. Required because the worker mounts the
  backend at `/app` and scripts at `/opt/scripts` (split mounts, not a unified
  repo tree) — so the script's repo-root path math would otherwise find **0**
  migration files inside the worker and fail its own assertion. CI is
  unaffected (env unset → identical behavior).
- **`brain/Dockerfile`** — `COPY` the new module + mirror it into
  `/app/brain/` (the two-line pattern every brain module follows).
- **New migration** `…_seed_restore_test_settings.py` — seed the
  `restore_test_*` defaults (non-secret). Modeled on
  `20260531_120000_seed_anomaly_probe_settings.py`. Brain probe settings are
  seeded via the worker migration runner (confirmed: none live in
  `brain/seed_app_settings.json`).
- **`docs/operations/backups.md`** — a "Restore test" section, parallel to the
  existing backup-watcher section.

### Failure taxonomy (the "no false pages" contract)

Maps onto the `operator_notifier` severity matrix
(`error`/`critical` → Telegram + Discord + log; `warning`/`info` →
Discord + log only):

| Condition                                                                                    | Severity  | Reaches                      |
| -------------------------------------------------------------------------------------------- | --------- | ---------------------------- |
| Restore clean, all row-count asserts + smoke pass                                            | `info`    | audit / Discord              |
| **Verification** fail (critical table empty/missing, smoke failed, restore produced no data) | `error`   | **Telegram** + Discord + log |
| **Infra** fail (docker unreachable, image missing, no dump found, container wouldn't start)  | `warning` | Discord + log                |
| Previously failing → now passes                                                              | `info`    | recovery notify              |

Rationale: a _corrupt backup_ is the real alarm (Telegram); a _docker hiccup
that prevented the test from running_ is signal-grade noise (Discord) — paging
Telegram on transient infra blips trains the operator to ignore Telegram, the
exact failure the severity matrix prevents. A module-level last-status guards a
recovery notify when a previously-failing test starts passing.

### Verdict policy (`_decide_verdict`)

The one place with a genuine judgment call rather than a mechanical answer:
how strictly to combine three signals into pass/fail. Specifically, a
custom-format `pg_restore` into a fresh DB routinely emits a **benign non-zero
exit** (role/ownership notices with `--no-owner`), so exit-code is _not_
authoritative — the row counts + smoke are. The policy body is the
security/UX decision that determines whether this probe is trustworthy or a
nuisance; it will be authored deliberately during implementation with the
captured signals in hand (`restore_exit`, `restore_stderr`, `row_counts`,
`smoke_ok`).

## app_settings keys

| Key                                     | Default                        | Notes                                        |
| --------------------------------------- | ------------------------------ | -------------------------------------------- |
| `restore_test_enabled`                  | `true`                         | Master switch                                |
| `restore_test_interval_hours`           | `24`                           | Daily cadence; gated via audit_log           |
| `restore_test_backup_dir`               | `/host-backups/auto`           | Brain's `:ro` mount root                     |
| `restore_test_tier`                     | `daily`                        | Subdir under backup_dir                      |
| `restore_test_postgres_image`           | `pgvector/pgvector:pg16`       | Must match prod (pgvector)                   |
| `restore_test_run_migrations_smoke`     | `true`                         | Disable the cross-container smoke if flaky   |
| `restore_test_critical_tables`          | `posts,app_settings,audit_log` | Comma-separated; name-validated              |
| `restore_test_min_row_count`            | `1`                            | Per-table floor                              |
| `restore_test_pg_ready_timeout_seconds` | `60`                           | Wait for the throwaway to accept connections |
| `restore_test_restore_timeout_seconds`  | `300`                          | `pg_restore` subprocess cap                  |
| `restore_test_smoke_timeout_seconds`    | `180`                          | migrations_smoke subprocess cap              |

Container/worker names (`poindexter-restore-test`, `poindexter-worker`) and the
in-worker script path (`/opt/scripts/ci/migrations_smoke.py`) are module
constants, not settings — same rationale as the sibling probes (changing them
requires coordinated edits elsewhere).

## Async hygiene

Unlike `backup_watcher` (which calls blocking `subprocess.run` + `time.sleep`
directly inside its async probe), the docker subprocess seams here are invoked
via `asyncio.to_thread` and readiness polling uses `asyncio.sleep`, honoring
the repo's "never block the event loop" principle. The once/daily heavy run
adds ~1–3 min to _that_ cycle — within the daemon's tolerance (backup_watcher's
retry sleeps already cost more), and bounded by the per-step timeout settings.

## Testing

`src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py`, mirroring
`test_backup_watcher.py`. Every docker/subprocess op is an injectable seam
(`find_dump_fn`, `run_container_fn`, `wait_ready_fn`, `restore_fn`, `smoke_fn`,
`query_count_fn`, `teardown_fn`, `notify_fn`, `now_fn`), so the suite drives
all paths **without touching real Docker**:

- happy path (restore + asserts + smoke all pass) → `info`, audit `completed`
- corrupt dump (restore yields empty/missing tables) → `error`, Telegram
- one critical table empty → `error`
- migrations_smoke fails → `error`
- no dump found → `warning`
- docker unreachable / container won't start → `warning`, teardown still runs
- daily gating: recent run present → `skipped` (no docker calls)
- disabled → `skipped`
- recovery: previous fail, now passes → recovery notify
- table-name validation rejects an injection-y `restore_test_critical_tables`
- teardown always runs (assert `finally` cleanup even on mid-flow exception)

Plus targeted tests for the `migrations_smoke.py` `POINDEXTER_BACKEND_ROOT`
override (env set → uses it; env unset → falls back to `parents[2]`).

## Out of scope

- Off-machine (restic / `F:\`) restore verification — separate concern; this
  probe targets the local daily tier the issue names.
- Auto-remediation — a failed restore is operator-actionable (the dump is bad);
  there's nothing safe for the probe to "fix" automatically, so it pages and
  stops (same posture as `smart_monitor` / the `migration_drift` no-recover
  path).
- A Grafana panel — `audit_log` events make it queryable; a dashboard panel can
  follow if the signal proves useful (deferred, not blocking).
