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
- [poindexter#388](https://github.com/Glad-Labs/poindexter/issues/388):
  brain backup-watcher with auto-retry on failure, before the
  alert_dispatcher fires the page.
