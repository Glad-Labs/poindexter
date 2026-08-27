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
Tier 2 streams a fresh, encrypted copy of the database **off-machine** to
any S3-compatible bucket (Backblaze B2, AWS S3, Cloudflare R2, MinIO) via
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
3. **First backup (acceptance gate)** — streams a fresh
   `pg_dump -Z0 | restic backup --stdin` (the same shape the runner uses
   below), so this first snapshot shares the runner's `(host, stdin-filename)`
   parent key and the runner's very first tick dedupes against it instead of
   re-ingesting the whole dump. If the `poindexter-backup` image or the
   postgres network isn't available it falls back to a pinned-`restic` backup
   of the latest daily dump. Either way, if this fails _nothing is persisted_
   — you fix the problem and re-run.
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
service (alpine + restic, reusing `scripts/Dockerfile.backup`). On its cron
it:

- streams a fresh **uncompressed** `pg_dump --format=custom -Z0` straight
  into `restic backup --stdin`, stamping an `audit_log` heartbeat
  (`offsite_backup_succeeded`) on success;
- backs up the **config surface** as a second snapshot in the same repo
  (`--tag config`), stamping `offsite_config_backup_succeeded`;
- once a week runs `restic check --read-data-subset=<pct>%` against the
  remote to catch **bit-rot**, stamping `offsite_backup_verified`.

> **Why the config snapshot exists (poindexter#889).** Until 2026-08-27 this
> runner shipped exactly one thing — a `pg_dump`. Everything else that is
> irreplaceable rode **only** on the Tier 3 DR USB job: `~/.poindexter`
> (whose `bootstrap.toml` holds `poindexter_secret_key`) and `~/.claude`
> (the memory tree). When that USB drive was removed, every copy of the
> secret key collapsed onto a single partition — and #889 is exactly the
> trap that springs then: **lose that key and the encrypted `app_settings`
> rows holding this repo's own restic password and S3 credentials cannot be
> decrypted, so the healthy DB snapshot becomes unopenable.** The config
> snapshot closes the partial-loss case (disk dies, credentials survive
> off-machine). It does _not_ by itself break the #889 cycle — opening the
> repo still requires the restic password held out-of-band, so
> [Store the offsite credentials OFF the machine](#-store-the-offsite-credentials-off-the-machine)
> remains mandatory, not optional. restic encrypts at rest, so shipping
> secret-bearing config here is defensible; it is the same content the USB
> tier already carried.
>
> The compose files bind `~/.poindexter` → `/config/poindexter` and
> `~/.claude` → `/config/claude`, both **read-only**. The runner excludes the
> derived bulk (Tier 1 dumps, rendered video/images, the deploy clone,
> venvs, logs). A configured path that isn't mounted is skipped with a
> warning; **all** paths missing raises a `warning` alert rather than
> reporting an empty success. A config-backup failure is warning-level and
> non-fatal — the DB snapshot has already succeeded by then, so prune/verify
> still run.
>
> **Measured payload (2026-08-27), and the knob to turn if B2 grows:**
>
> | Path            | Raw    | After excludes |
> | --------------- | ------ | -------------- |
> | `~/.poindexter` | ~29 GB | **35 MB**      |
> | `~/.claude`     | 340 MB | **339 MB**     |
>
> The `~/.claude` figure is ~329 MB of session transcripts under `projects/`;
> the genuinely irreplaceable part (the `memory/` trees) is **2.7 MB**.
> Transcripts are write-once, so restic dedupes them and each later snapshot
> adds only new files — but `offsite_backup_prune_enabled` defaults to
> `false` (append-only posture), so that lineage grows without bound. B2's
> free tier is 10 GB and has been breached once before (2026-07-16). If the
> repo approaches the cap, the cheapest trim is adding
> `/config/claude/projects` to `offsite_backup_config_excludes` — a
> single-row settings change, no deploy — which keeps `settings.json`,
> plugins, and agent memory while dropping the transcript bulk. Note this
> also drops the per-project `memory/` trees, so pair it with a separate
> backup of those if you take it.

> **Why an uncompressed dump, not `restic backup` of the Tier 1 files?**
> Tier 1 writes `pg_dump --format=custom` (zlib-compressed). restic dedupes
> and compresses via content-defined chunking, and compressed bytes defeat
> both — a one-row change reshuffles the whole compressed stream, so every
> daily dump reads as 100% new data. Measured 2026-07-11: **1.01× restic
> compression, ~150–230 MiB added per dump, repo at 8.3 GiB across 62
> snapshots after 25 days** (append-only, never pruned) — on track to
> breach B2's 10 GB free cap in ~1–2 weeks. Feeding restic an **uncompressed**
> dump lets it dedupe the ~unchanged bulk day-over-day and compress its own
> packs, so the repo holds near the live DB size (~1 GB) regardless of
> snapshot count. The runner takes its own dump (it already has psql/pg_dump
> connectivity) rather than re-reading Tier 1's files, so Tier 1's dumps,
> retention, and restore-test are left untouched. `set -o pipefail` surfaces
> a mid-stream `pg_dump` failure even if restic exits 0 on the truncated
> input, so a half-streamed dump alerts instead of saving a short snapshot.
>
> This change slows growth going forward; it does **not** shrink the existing
> repo. To reclaim space already stored under the old scheme, prune once (see
> below) or start a fresh repo path.

`start-stack.sh` decrypts the three secrets into a git-ignored
`.poindexter-backup-offsite.env` on every `up`/restart, so the runner picks
up credentials without any `.env` you maintain by hand.

### Append-only posture (ransomware resilience)

The runner is **backup-only** — it never issues `restic forget`/`prune`
(which delete objects), so a write-only S3 key (no `deleteFiles`) is
sufficient and is the recommended configuration. With the streamed
uncompressed dump above, per-snapshot growth is a small delta, so the
append-only repo stays under B2's free cap for a long time without any
pruning at all.

**Do NOT bound a restic repo with a raw age-based bucket lifecycle rule.**
restic stores data in immutable pack files that stay referenced by future
snapshots indefinitely; a "delete objects older than N days" lifecycle rule
deletes live packs and **corrupts the repo**. The only safe way to reclaim
space is restic's own `forget --prune`, which needs a delete-capable key —
enable it via the `offsite_backup_prune_enabled` escape hatch (default
`false`). To keep the ransomware guarantee while using a delete-capable key,
put the bucket under **Object Lock / WORM** (a compliance-mode retention
window bounds how long a compromised host could hold deletion off), or run
the prune from a separate trusted context. See the B2 reclaim steps in the
2026-07 offsite-dedup PR for the recommended one-time cleanup.

**Before relying on `forget --prune` to reclaim space, confirm the bucket
has a _version-expiry_ lifecycle rule — a different setting from the
age-based rule warned against above.** `forget --prune`'s deletes only hide
the current version of an object; B2 keeps every prior version of every
object indefinitely unless told otherwise, so without a rule expiring
hidden/previous versions, the "reclaimed" space stays fully billed and fully
counted against the Daily Storage Cap. (Confirmed 2026-07-16: a prune took
restic's own view from 62 snapshots/8.3 GiB down to 16/1.8 GiB, but B2's
reported usage stayed at 10.1 GB — the whole gap was retained hidden
versions.) Set the bucket's file-lifecycle setting to **"Keep only the last
version of the file"** — this only purges already-hidden versions and never
touches a live/current object, so it's safe to apply even with snapshots
still referencing other files in the same bucket.

### Operator commands

```bash
poindexter backup status      # repo + last-backup / last-verify ages
poindexter backup run         # trigger an offsite backup now
poindexter backup verify      # restic check --read-data-subset now
poindexter backup snapshots   # list remote snapshots
```

### ⚠️ Store the offsite credentials OFF the machine

**A backup you cannot open is not a backup.** The offsite repository's
credentials are stored as encrypted `app_settings` rows
(`offsite_backup_restic_password`, `offsite_backup_s3_access_key_id`,
`offsite_backup_s3_secret_access_key`) — that is, **inside the database the
backup contains** — and the key that decrypts them (`poindexter_secret_key`)
lives only in `~/.poindexter/bootstrap.toml`, which **the runner does not back
up**. The runner streams exactly one `pg_dump` and nothing else.

So after a total loss the only surviving artifact is the restic repository, and
opening it requires three values that existed only on the machine you lost:

| Needed to open the repo                      | Where it lives today           | Survives total loss? |
| -------------------------------------------- | ------------------------------ | -------------------- |
| restic password                              | encrypted `app_settings` row   | ❌ inside the backup |
| S3 access key id / secret                    | encrypted `app_settings` rows  | ❌ inside the backup |
| `poindexter_secret_key` (decrypts the above) | `~/.poindexter/bootstrap.toml` | ❌ never backed up   |

This is easy to miss because **every signal stays green**: `backup run` succeeds,
snapshots are created and retained, and `backup verify` passes. Those prove the
repo is _writable and intact_ — never that it is _reachable without this
machine_.

**Copy the restic password, the S3 key pair, and the repository URL into a
password manager (or print them) the day you run `backup setup`.**

### Recovery drill — prove you can actually get back in

```bash
export RESTIC_PASSWORD=... AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
poindexter backup verify-recovery --repository s3:https://s3.us-east-005.backblazeb2.com/my-bucket
```

`verify-recovery` reads **nothing** from this install — not the database, not
`bootstrap.toml`, not `app_settings`. You supply the credentials you keep
off-machine and it lists the snapshots they can open. Any option you omit is
prompted for with hidden input, so nothing lands in shell history.

A pass means the recovery path is real. A failure means you have just found out
your backup is unrecoverable _while you still have a working machine to fix it
from_ — which is the entire point of running it. Run it from a different machine
for a true drill; running it here still proves the credentials are correct and
complete, which is the part that actually gets missed.

Re-run it after any credential rotation, and periodically regardless — a backup
is only proven by a recovery drill performed without access to the source
machine.

### Settings (`app_settings`)

All Tier 2 tunables are DB-backed (seeded every boot, so they reach
existing deployments — only the three secrets are written by the wizard):

| Setting                                          | Default                             | Notes                                                                                                                        |
| ------------------------------------------------ | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `offsite_backup_enabled`                         | `true`                              | Master switch for the runner                                                                                                 |
| `offsite_backup_interval`                        | `24h`                               | Backup cadence (`<N>{s\|m\|h\|d}`)                                                                                           |
| `offsite_backup_source_tier`                     | `daily`                             | Snapshot `--tag` only (advisory since the 2026-07 streamed-dump change; the runner dumps live, no longer reads a Tier 1 dir) |
| `offsite_backup_repository`                      | _(set by wizard)_                   | `s3:https://<endpoint>/<bucket>/<path>`                                                                                      |
| `offsite_backup_s3_region`                       | _(set by wizard)_                   | SigV4 signing region — required for non-us-east-1 buckets (e.g. B2 `us-east-005`); the wizard derives it from the endpoint   |
| `offsite_backup_restic_host`                     | `poindexter`                        | Stable `restic backup --host` — container hostnames change on recreate, which would break parent-snapshot selection          |
| `offsite_backup_restic_image`                    | `restic/restic:0.16.4`              | Pinned restic image (runner + wizard use the same version)                                                                   |
| `offsite_backup_config_enabled`                  | `true`                              | Master switch for the second (`--tag config`) snapshot — bootstrap.toml + the memory tree (poindexter#889)                   |
| `offsite_backup_config_paths`                    | `/config/poindexter,/config/claude` | CSV of **in-container** mount points (not host paths); bound read-only by the compose files                                  |
| `offsite_backup_config_excludes`                 | _(see defaults)_                    | CSV of restic `--exclude` patterns — the derived bulk (dumps, video, images, deploy clone, venvs, logs)                      |
| `offsite_backup_keep_daily`                      | `7`                                 | Retention (only applied if pruning is enabled)                                                                               |
| `offsite_backup_keep_weekly`                     | `4`                                 |                                                                                                                              |
| `offsite_backup_keep_monthly`                    | `6`                                 |                                                                                                                              |
| `offsite_backup_prune_enabled`                   | `false`                             | Escape hatch — re-enables delete-bearing `forget`/`prune`                                                                    |
| `offsite_backup_verify_enabled`                  | `true`                              | Weekly `restic check`                                                                                                        |
| `offsite_backup_verify_interval_hours`           | `168`                               | Verify cadence (168h = weekly)                                                                                               |
| `offsite_backup_verify_read_data_subset_percent` | `5`                                 | Fraction of pack data re-read each verify (bit-rot scan)                                                                     |
| `offsite_backup_max_age_hours`                   | `26`                                | Staleness threshold for the brain watch (24h cadence + slack)                                                                |
| `offsite_backup_watch_enabled`                   | `true`                              | Brain auto-retry watch master switch                                                                                         |
| `offsite_backup_watch_max_retries`               | `2`                                 | Cumulative restarts across cycles before escalation                                                                          |
| `offsite_backup_watch_retry_delay_seconds`       | `120`                               | Wait between `docker restart` and the post-restart re-read                                                                   |

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
not three. The alert description includes a truncated tail of restic's
actual stderr, not just the exit code, so the real cause (credentials,
network, a B2 cap) doesn't require digging through `docker logs
poindexter-backup-offsite` to find. A subsequent successful backup
auto-resolves the firing row via `brain/offsite_backup_watch.py`'s
fresh-heartbeat check, the same way it already resolves `offsite_backup_stale`.

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

> **⚠️ The sentinel only covers script failures, not the script never
> running.** A sentinel is written _by_ the dr-backup script, so anything
> that stops the script from starting is invisible to this path. The
> systemd units carry `RequiresMountsFor=/mnt/dr-usb`, and the fstab entry
> carries `nofail` — so with the USB drive absent the unit fails at the
> _dependency_ level, the script never executes, no sentinel is written, and
> the brain sees nothing at all. This is not hypothetical: it hid a dead
> Tier 3 for at least a week in August 2026, visible only as hourly
> `Dependency failed` lines in `journalctl`. If you rely on Tier 3, monitor
> the _timer_, not just the sentinel.

### Tier 3 (DR USB) — PARKED 2026-08-27

The DR USB timers are **deliberately disabled**, not broken: the stick was
failing and was removed pending a replacement drive. `systemctl list-timers`
will not show them, and that is the expected state — do not page on it.

```bash
systemctl disable --now poindexter-dr-backup.timer poindexter-dr-backup-hourly.timer
```

The unit files stay installed at `/etc/systemd/system/`, so re-arming on the
replacement drive is one command (after adding the new UUID to `/etc/fstab`
at `/mnt/dr-usb`):

```bash
sudo systemctl enable --now poindexter-dr-backup.timer poindexter-dr-backup-hourly.timer
```

**What parking costs you:** Tier 3 was the only tier covering `~/.poindexter`
and `~/.claude` until the Tier 2 config snapshot above was added on the same
day. With that in place, the config surface is still backed up off-machine
daily — but Tier 3 remains the only _local, offline_ copy, and offline is the
tier that survives a credential compromise or an account lockout. Treat the
replacement drive as owed work, not optional.

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
