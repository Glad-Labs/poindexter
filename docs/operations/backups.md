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

Same-drive backups don't survive drive failure. For users who care
about hardware-loss durability, two patterns work today:

- **USB / external drive via restic**: Matt's setup. Encrypted,
  deduped, retention-managed. See `~/.poindexter/scripts/dr-backup/`
  in the operator overlay (private). Public docs for a
  `poindexter setup --backup-target` interactive wizard are tracked
  in [poindexter#386](https://github.com/Glad-Labs/poindexter/issues/386).
- **Cloud (S3 / B2 / R2) via restic**: ~$1/mo at our typical scale
  ($0.005/GB/mo on Backblaze B2). Same `restic` engine, different
  `repo` arg. Ship gate is the wizard issue above.

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

- [poindexter#386](https://github.com/Glad-Labs/poindexter/issues/386):
  `poindexter setup --backup-target` interactive wizard for Tier 2.
- [poindexter#387](https://github.com/Glad-Labs/poindexter/issues/387):
  brain daemon SMART monitoring — surface drive-failing-soon warnings
  before drives actually die.
