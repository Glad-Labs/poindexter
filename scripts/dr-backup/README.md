# DR backup — local encrypted snapshots to removable media

A restic-based disaster-recovery tier that backs up **host paths** (config,
source trees, operator memory) plus a fresh `pg_dump`, to a removable drive.

This is deliberately separate from the containerised backup tiers in
`scripts/backup/` and `scripts/backup-offsite/`. Those run inside Docker and
back up **one database**; this one runs on the host and captures the files
those tiers cannot see — including the operator config that makes a restore
decryptable. See Glad-Labs/poindexter#891 for why that distinction matters.

## Layout

| file                | what                                                              |
| ------------------- | ----------------------------------------------------------------- |
| `run-backup.sh`     | daily full run — host trees + `pg_dump`, tag `dr-daily`           |
| `run-hourly-pg.sh`  | hourly `pg_dump` only, tag `pg-hourly`                            |
| `register-task.ps1` | Windows installs: registers the daily run as a Task Scheduler job |

Scheduling on Linux lives in `infrastructure/systemd/` —
`poindexter-dr-backup.timer` (daily full run) +
`poindexter-dr-backup-hourly.timer` (hourly pg), which set the `DR_*` env
overrides for the Linux host. On Windows, `register-task.ps1` registers the
equivalent Task Scheduler job (kept for Windows installs — glad-labs-stack#3324).

The two tags are independent: each run's `forget` is tag-scoped, so the daily
prune never touches hourly snapshots and vice versa. A third tag added later
is untouched by both.

## Configuration

No config file. Paths resolve from `$HOME` with env overrides:

| variable          | default                | notes                                      |
| ----------------- | ---------------------- | ------------------------------------------ |
| `DR_RESTIC_BIN`   | `$HOME/bin/restic.exe` | on Linux set to `restic`                   |
| `DR_BACKUP_REPO`  | `F:/poindexter-backup` | restic repo URI                            |
| `DR_BACKUP_MOUNT` | `/f/poindexter-backup` | stat-able path for the drive-present check |
| `POINDEXTER_HOME` | `$HOME/.poindexter`    | bootstrap + logs                           |

`DR_BACKUP_MOUNT` exists because Git Bash cannot `stat` a `F:/…` path; on
Linux both variables point at the same mountpoint. Prefer a UUID-keyed
`/etc/fstab` entry over `/media/$USER/…` so a systemd timer does not depend on
a desktop session having automounted the drive.

## What gets backed up

- `~/.poindexter/` — operator config incl. `bootstrap.toml`, logs, these scripts
- `~/glad-labs-website/`, `~/glad-labs-prompts/` — source trees with full `.git`
- `~/.claude/` — operator memory and session history
- a fresh `pg_dump` of the primary database, taken at run time

Excluded: `~/.poindexter/backups` (already covered by the dedicated tiers, and
including it would double-count several GB per snapshot), plus the usual
rebuildable trees — `node_modules`, `.next`, `.venv`, `dist`, `build`,
`__pycache__`, `*.pyc`, and the various tool caches.

> `~/.claude/` was **missing from this list for three months** and was added
> 2026-07-19. Nothing reported a problem: the snapshot list was healthy for the
> paths it knew about. If you port or rewrite these scripts, verify coverage by
> reading the archive back — `restic ls <snap> | grep -c memory/` — not by
> checking that the job exited 0.

## Restore

```bash
export RESTIC_PASSWORD="$(grep '^poindexter_backup_passphrase' \
  ~/.poindexter/bootstrap.toml | cut -d'"' -f2)"
export DR_BACKUP_REPO="${DR_BACKUP_REPO:-F:/poindexter-backup}"

restic -r "$DR_BACKUP_REPO" snapshots                    # list
restic -r "$DR_BACKUP_REPO" restore latest --target /tmp/restore

# just the database, for "help, I nuked the DB"
restic -r "$DR_BACKUP_REPO" restore latest \
  --target /tmp/restore-db --include '**/poindexter_brain.sql'
```

## Retention

7 daily / 4 weekly / 6 monthly for `dr-daily`; last 24 for `pg-hourly`. Pruned
at the end of every run.

`--group-by "host,tags"` is required on both `forget` calls. Each run dumps to
a fresh `mktemp` path, so without it restic treats every snapshot as its own
group and the count-based policies keep everything forever.

## Secrets, and the recovery trap

The repo passphrase is `poindexter_backup_passphrase` in
`~/.poindexter/bootstrap.toml`. **restic has no backdoor** — lose the
passphrase and the snapshots are permanently unreadable.

`bootstrap.toml` is itself inside the snapshots. That is worth doing, because
it means opening the repo returns the master key and every service credential
rather than a database you cannot decrypt.

> ⚠️ **It is not a mitigation for losing the passphrase, and an earlier version
> of this README claimed it was.** You need the passphrase to open the repo
> before you can read anything inside it, so a copy of the passphrase stored
> inside the repo is unreachable in exactly the scenario where you need it.
> The same circular reasoning is the subject of Glad-Labs/poindexter#889.
>
> The only real mitigation is a copy of the passphrase held **outside both the
> machine and the drive** — a password manager, or printed and stored offline.
> And an untested recovery path is not a recovery path: prove it from hardware
> with no access to the source machine, using only the stored passphrase.
> Verifying from the machine being backed up proves nothing, because that
> machine supplies the credentials whose loss is the whole scenario.
