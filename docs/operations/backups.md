# Backups

Poindexter backs up its Postgres state in tiers. Tier 1 ships in the
default Docker stack — `docker compose up` and you're protected against
accidental drops, migration mishaps, and container wipes. Tier 2 is
opt-in for off-machine durability (drive failure, theft, ransomware).

## Tier 1 — in-stack hourly + daily dumps

Two compose services, one tiny image (`scripts/Dockerfile.backup`):

| Service         | Cadence | Retention | Path                                 |
| --------------- | ------- | --------- | ------------------------------------ |
| `backup-hourly` | 1h      | 24 dumps  | `~/.poindexter/backups/auto/hourly/` |
| `backup-daily`  | 24h     | 7 dumps   | `~/.poindexter/backups/auto/daily/`  |

Both write `pg_dump --format=custom` into a **bind-mounted host
directory** (NOT a docker named volume). Bind mounts survive
`docker volume prune` and Docker Desktop reinstalls — the failure mode
that lost a day of state on 2026-05-05 and prompted this work.

Each tier reads its config from `app_settings` at every tick — no
container restart needed when you tune cadence or retention:

| Setting                   | Default | Notes                                        |
| ------------------------- | ------- | -------------------------------------------- |
| `backup_hourly_enabled`   | `true`  | Skip ticks without stopping the loop         |
| `backup_daily_enabled`    | `true`  |                                              |
| `backup_hourly_interval`  | `1h`    | `<N>{s\|m\|h\|d}`                            |
| `backup_daily_interval`   | `24h`   |                                              |
| `backup_hourly_retention` | `24`    | Older dumps pruned after each successful run |
| `backup_daily_retention`  | `7`     |                                              |

Override the host directory by setting `POINDEXTER_BACKUP_DIR`
(e.g. to a second drive) before `docker compose up`.

### Restore

```bash
# pick the dump
ls ~/.poindexter/backups/auto/hourly/

# restore (drop+recreate first if the DB exists)
docker exec -i poindexter-postgres-local pg_restore \
    -U poindexter -d poindexter_brain --clean --if-exists \
    < ~/.poindexter/backups/auto/hourly/poindexter_brain_20260505T160500Z.dump
```

## Tier 2 — off-machine (optional, recommended)

Same-drive backups don't survive drive failure, theft, or ransomware.
Tier 2 ships Tier 1's daily dumps **off-machine** to any S3-compatible
bucket (Backblaze B2, AWS S3, Cloudflare R2, MinIO) via
[restic](https://restic.net) — encrypted, deduplicated, retention-managed.
At our scale it runs ~$1/mo ($0.005/GB/mo on B2).

### Setup wizard

```bash
poindexter backup setup
```

The wizard is **staged so nothing is saved until a real backup succeeds**:

1. **Append-only key check** (advisory) — probes whether the S3 key can
   `DeleteObject`. An append-only key (one that _cannot_ delete) is
   strongly recommended: a ransomed host can then write new snapshots but
   cannot destroy backup history. If the key is delete-capable the wizard
   warns and asks for explicit confirmation.
2. **`restic init`** — creates the encrypted repo.
3. **First backup (acceptance gate)** — backs up the latest daily dump.
   If this fails, _nothing is persisted_ — you fix the problem and re-run.
4. **Encrypted persist** — writes the repo URL (plaintext) and the restic
   password + S3 key pair (encrypted via pgcrypto) to `app_settings`, then
   prints the restic password once for you to save offline.

> ### ⚠️ Save the restic password offline — now
>
> The wizard generates a high-entropy restic repository password and stores
> it encrypted in `app_settings`. **In a drive-failure / theft / ransomware
> event the database and this machine are gone**, so a copy that lives only
> in the DB is no copy at all. Write the printed password to your password
> manager / a fireproof safe. Without it the remote repo is **unrecoverable**
> — restic encryption with a lost password is final.

### The `backup-offsite` runner

`poindexter backup setup` configures an in-stack `backup-offsite` compose
service (alpine + restic, reusing `scripts/Dockerfile.backup`). It reads
`~/.poindexter/backups/auto/daily/` **read-only**, and on its cron:

- runs `restic backup` of the latest daily dump, stamping an `audit_log`
  heartbeat (`offsite_backup_succeeded`) on success;
- once a week runs `restic check --read-data-subset=<pct>%` against the
  remote to catch **bit-rot**, stamping `offsite_backup_verified`.

`start-stack.sh` decrypts the three secrets into a git-ignored
`.poindexter-backup-offsite.env` on every `up`/restart, so the runner picks
up credentials without any `.env` you maintain by hand.

### Append-only posture (ransomware resilience)

The runner is **backup-only** — it never issues `restic forget`/`prune`
(which delete objects), so a write-only S3 key (no `deleteFiles`) is
sufficient and is the recommended configuration. Manage retention on the
bucket side instead: a B2 lifecycle rule or Object Lock / WORM. If you
genuinely need restic-side pruning, the `offsite_backup_prune_enabled`
escape hatch (default `false`) re-enables it — but then your key needs
delete and you lose the ransomware guarantee.

### Operator commands

```bash
poindexter backup status      # repo + last-backup / last-verify ages
poindexter backup run         # trigger an offsite backup now
poindexter backup verify      # restic check --read-data-subset now
poindexter backup snapshots   # list remote snapshots
```

### Settings (`app_settings`)

All Tier 2 tunables are DB-backed (seeded every boot, so they reach
existing deployments — only the three secrets are written by the wizard):

| Setting                                          | Default                | Notes                                                                                                                      |
| ------------------------------------------------ | ---------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `offsite_backup_enabled`                         | `true`                 | Master switch for the runner                                                                                               |
| `offsite_backup_interval`                        | `24h`                  | Backup cadence (`<N>{s\|m\|h\|d}`)                                                                                         |
| `offsite_backup_source_tier`                     | `daily`                | Which Tier 1 dir to ship (`daily` / `hourly`)                                                                              |
| `offsite_backup_repository`                      | _(set by wizard)_      | `s3:https://<endpoint>/<bucket>/<path>`                                                                                    |
| `offsite_backup_s3_region`                       | _(set by wizard)_      | SigV4 signing region — required for non-us-east-1 buckets (e.g. B2 `us-east-005`); the wizard derives it from the endpoint |
| `offsite_backup_restic_image`                    | `restic/restic:0.16.4` | Pinned restic image (runner + wizard use the same version)                                                                 |
| `offsite_backup_keep_daily`                      | `7`                    | Retention (only applied if pruning is enabled)                                                                             |
| `offsite_backup_keep_weekly`                     | `4`                    |                                                                                                                            |
| `offsite_backup_keep_monthly`                    | `6`                    |                                                                                                                            |
| `offsite_backup_prune_enabled`                   | `false`                | Escape hatch — re-enables delete-bearing `forget`/`prune`                                                                  |
| `offsite_backup_verify_enabled`                  | `true`                 | Weekly `restic check`                                                                                                      |
| `offsite_backup_verify_interval_hours`           | `168`                  | Verify cadence (168h = weekly)                                                                                             |
| `offsite_backup_verify_read_data_subset_percent` | `5`                    | Fraction of pack data re-read each verify (bit-rot scan)                                                                   |
| `offsite_backup_max_age_hours`                   | `26`                   | Staleness threshold for the brain watch (24h cadence + slack)                                                              |
| `offsite_backup_watch_enabled`                   | `true`                 | Brain auto-retry watch master switch                                                                                       |
| `offsite_backup_watch_max_retries`               | `2`                    | Cumulative restarts across cycles before escalation                                                                        |
| `offsite_backup_watch_retry_delay_seconds`       | `120`                  | Wait between `docker restart` and the post-restart re-read                                                                 |

The three secrets — `offsite_backup_restic_password`,
`offsite_backup_s3_access_key_id`, `offsite_backup_s3_secret_access_key` —
are `is_secret=true` (pgcrypto-encrypted) and are written by the wizard, not
seeded.

### Brain offsite-backup watch (auto-retry before paging)

`brain/offsite_backup_watch.py` (poindexter#386) is the self-heal layer for
the offsite tier — a sibling of `backup_watcher` with one difference: its
freshness source is the `audit_log` heartbeat (`offsite_backup_succeeded`), a
**creds-free** DB read, so the brain never touches the restic password. Each
cycle it reads the heartbeat age; if it's past `offsite_backup_max_age_hours`
it `docker restart`s `poindexter-backup-offsite`, waits, and re-reads. After
`offsite_backup_watch_max_retries` cumulative failures it emits a firing
`offsite_backup_stale` alert (`critical`) and stops kicking. Unlike
`backup_watcher` — which leans on the runner's own failure alert plus the
Tier 1 healthcheck — the offsite tier has no other alert source for a dead
runner, so this watch emits its own firing alert on escalate.

### Restore from the remote

When the machine is gone, restore from the remote repo with the offline
restic password — see the **DB-4** runbook in
[`disaster-recovery.md`](./disaster-recovery).

## Failure handling

The runner inserts a row into `alert_events` (severity=critical) on
any non-zero exit. The brain daemon's `alert_dispatcher` poll picks it
up on its 30s sweep and routes through the same Telegram (critical) +
Discord (warning) pipeline Grafana alerts use — one notification surface,
not three.

If the failure is "postgres is unreachable", the alert insert itself
will fail (chicken-and-egg). The container's healthcheck catches that
case: it flips to unhealthy if the latest hourly dump is > 90 minutes
old, which Grafana surfaces directly via the standard container-down
alert path.

### Brain backup-watcher (auto-retry before paging)

`brain/backup_watcher.py` (Glad-Labs/poindexter#388) sits between a
backup failure and the operator's phone. Every cycle it stats the
newest dump in each tier; if either is past its threshold it
`docker restart`s the relevant container, waits the configured delay,
and re-stats. When a fresh dump appears it writes a
`status='resolved'` row to `alert_events` so the dispatcher pages the
operator with `[RESOLVED · ...]` instead of leaving them wondering. If
the retry budget is exhausted without recovery, the watcher backs off
and lets the original firing alert stand — the operator still gets
paged, just on the actual problem rather than on a transient hiccup.

| Setting                                 | Default                      | Notes                                                      |
| --------------------------------------- | ---------------------------- | ---------------------------------------------------------- |
| `backup_watcher_enabled`                | `true`                       | Master switch                                              |
| `backup_watcher_poll_interval_minutes`  | `5`                          | Cadence; matches the brain cycle                           |
| `backup_watcher_hourly_max_age_minutes` | `90`                         | Hourly staleness threshold (matches container healthcheck) |
| `backup_watcher_daily_max_age_hours`    | `26`                         | Daily staleness threshold (24h cadence + 90 min slack)     |
| `backup_watcher_max_retries`            | `2`                          | Cumulative across cycles before escalation                 |
| `backup_watcher_retry_delay_seconds`    | `120`                        | Wait between `docker restart` and the post-restart re-stat |
| `backup_watcher_backup_dir`             | `~/.poindexter/backups/auto` | Host path where the backup containers write dumps          |
| `backup_watcher_sentinel_dir`           | `/host-backup-logs`          | Container path of the sentinel scan dir (#444)             |

### dr-backup sentinel surfacing (#444)

The host-side dr-backup scripts at `~/.poindexter/scripts/dr-backup/`
write a `dr-backup-*-failed.sentinel` file under `~/.poindexter/logs/`
when both:

1. the script itself failed (non-zero exit), AND
2. the script's primary Telegram alert path failed too (creds missing,
   postgres down, network broken).

The sentinel is the second line of defense — the assumption is that
brain's backup-watcher will pick it up on its next sweep and surface
the failure through whatever channel still works.

`brain/backup_watcher.py` scans the configured `backup_watcher_sentinel_dir`
each cycle and inserts a firing `alert_events` row for every sentinel it
finds, named `dr_backup_hourly_failed` or `dr_backup_daily_failed`. The
fingerprint embeds the sentinel's `ts` field so re-scans of the same
sentinel dedup — the operator gets exactly one page per failure
incident, not one per probe cycle. Cleanup is owned by the script side
(it `rm`s its own sentinel on the next successful run), so brain never
deletes files it didn't write.

The bind mount `~/.poindexter/logs:/host-backup-logs:ro` in
`docker-compose.local.yml` (under the `brain-daemon` service) is what
exposes the sentinel directory inside the container. If you change
`backup_watcher_sentinel_dir`, change the mount target to match.

### Restore test (does the dump actually restore?)

`brain/restore_test_probe.py` (Glad-Labs/poindexter#441) is the layer that
proves a dump _restores_, not just that it's _fresh_. Once per
`restore_test_interval_hours` (default 24h) the brain picks the newest dump
under `/host-backups/auto/daily/`, spins a throwaway `pgvector/pgvector:pg16`
container, `pg_restore`s the dump, re-runs the production migration runner
against it (`migrations_smoke.py`, via `docker exec` into the worker), asserts
the critical tables (`posts`, `app_settings`, `audit_log`) survived with rows
and `schema_migrations` is populated, then tears the throwaway down.

A **verification** failure (corrupt dump, empty table, smoke failure) pages at
`error` — "your latest backup may be corrupt". An **infra** failure (docker
unreachable, no dump found) is `warning` — Discord only, so a transient hiccup
that merely prevented the test doesn't train you to ignore Telegram. State
(last-run time) lives in `audit_log`, so a brain restart doesn't re-trigger the
heavy run. No new compose mounts — it reuses the docker socket and the
read-only `/host-backups` mount already wired for the backup-watcher.

| Setting                                 | Default                        | Notes                                      |
| --------------------------------------- | ------------------------------ | ------------------------------------------ |
| `restore_test_enabled`                  | `true`                         | Master switch                              |
| `restore_test_interval_hours`           | `24`                           | Daily cadence                              |
| `restore_test_backup_dir`               | `/host-backups/auto`           | Brain's read-only mount                    |
| `restore_test_tier`                     | `daily`                        | Subdir to read dumps from                  |
| `restore_test_postgres_image`           | `pgvector/pgvector:pg16`       | Must match prod (pgvector extension)       |
| `restore_test_run_migrations_smoke`     | `true`                         | Disable the cross-container smoke if flaky |
| `restore_test_critical_tables`          | `posts,app_settings,audit_log` | Comma-separated; name-validated            |
| `restore_test_min_row_count`            | `1`                            | Per-table floor                            |
| `restore_test_pg_ready_timeout_seconds` | `60`                           | Throwaway readiness wait                   |
| `restore_test_restore_timeout_seconds`  | `300`                          | `pg_restore` cap                           |
| `restore_test_smoke_timeout_seconds`    | `180`                          | migrations_smoke cap                       |

## Operational hygiene

- Disk: 24h × 128 MB ≈ 3 GB hourly + 7d × 128 MB ≈ 900 MB daily.
  Total ~4 GB at our current scale; multiply by your `posts` table
  growth.
- Healthcheck cadence: hourly tier is checked every 5 min; daily
  every 30 min. Both with 90-min staleness slack to avoid flapping
  during the legitimate gap between tick and rotation.
- Logs land in Docker's container log (visible via
  `docker logs poindexter-backup-hourly`). Promtail ships them to
  Loki for Grafana queries.

## Future

- [poindexter#387](https://github.com/Glad-Labs/poindexter/issues/387):
  brain daemon SMART monitoring — surface drive-failing-soon warnings
  before drives actually die.
- USB / external-drive Tier 2 backend (deferred from #386 — the
  Windows drive-letter→container mount needs its own design pass).
- A Grafana panel for the offsite tier (the `audit_log`
  `offsite_backup_succeeded` / `offsite_backup_verified` events make it
  queryable today).
