# Nightly sleep window

The operator box averaged **365 W at the wall around the clock** in October
2026 (about $75 a month at `electricity_rate_kwh` $0.288), with an idle floor
near 270 W, and did nothing useful overnight. Since 2026-10-18 it suspends to
RAM from **01:00 to 08:00** (`operator_timezone`) and wakes itself on the RTC
alarm.

## How it works

`poindexter-night-sleep.timer` runs `scripts/linux/night_sleep.py` every 10
minutes. Outside the window it exits at once. Inside it:

1. **Defers while anything is in flight**: a `poindexter-gpu:*` advisory-lock
   holder (a render or an LLM call), a `pipeline_tasks` row `in_progress`, a
   running `poindexter-deploy-sync.service`, or the operator's skip file
   `~/.poindexter/no-sleep-tonight`. Each tick re-checks. Past
   `host_sleep_defer_limit_minutes` (120) after the start it stays up for the
   night and writes a `host_sleep_skipped` audit row naming why.
2. **Unloads both GPUs.** The NVIDIA driver preserves VRAM across suspend
   (`PreserveVideoMemoryAllocations=1`) by copying it to `/var` on the 150 GB
   root drive: up to 56 GB across the 5090 and the 3090. So the scheduler's
   reclaim ladder runs, then every model on every Ollama instance is unloaded
   (the ladder deliberately skips the pinned 3090 judge).
3. **Suspends** with `rtcwake -m mem -t <wake>` (audit `host_sleep`).
4. **On resume**: writes `host_resume` (`slept_at`, `resumed_at`), checks that
   every container with a GPU device request can still run `nvidia-smi` and
   restarts any that cannot (`host_resume_checked`), then restarts the backup
   jobs in `host_sleep_resume_restart_containers`. Those jobs time their next
   run with `sleep`, which counts only awake time, so without the restart a
   "daily" backup would drift seven hours later every night. Restarted, each
   dumps at once: the daily, volume and off-site backups now run every
   morning after wake.

The backup watcher reads the last `host_resume` row and reports a stale tier
as `resume_grace` (no restart, no page) for `host_sleep_resume_grace_minutes`
(30), while the restarted jobs write their first dumps.

## Settings

| Key                                    | Default              | Meaning                                                         |
| -------------------------------------- | -------------------- | --------------------------------------------------------------- |
| `host_sleep_enabled`                   | `false`              | Master switch. Nothing sleeps until it is true.                 |
| `host_sleep_start`                     | `01:00`              | Window start, `HH:MM` in `operator_timezone`.                   |
| `host_sleep_wake`                      | `08:00`              | RTC wake time. At or before the start = crosses midnight.       |
| `host_sleep_defer_limit_minutes`       | `120`                | How long past the start a busy box may defer before staying up. |
| `host_sleep_resume_grace_minutes`      | `30`                 | Backup-watcher grace after a wake.                              |
| `host_sleep_resume_restart_containers` | the four backup jobs | Restarted on every wake.                                        |

## Operating it

```bash
bash scripts/linux/install-night-sleep.sh            # install + enable (from the deploy clone)
sudo python3 scripts/linux/night_sleep.py --dry-run  # what it would do right now
touch ~/.poindexter/no-sleep-tonight                 # stay up tonight (delete it after)
```

Every ops session and the demo bake run after 08:00
(`scripts/linux/install-session-timers.sh`,
`infrastructure/systemd/poindexter-demo-bake.timer`). A timer that fires
inside the window is replayed at wake by `Persistent=true`, so keep new ones
out of it.

## Not covered yet

Other freshness checks (Prometheus rules, brain probes) have no resume grace.
The first mornings will show which of them read a planned sleep as an
outage; give each the same `host_resume` grace rather than raising its
threshold.
