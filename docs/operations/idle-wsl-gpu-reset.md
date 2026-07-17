# Idle-only WSL/Docker GPU reset

**Status:** built (PR 2, 2026-07-12), **not yet activated**. Registration and
the first live run are a deliberate, supervised operator action — see
[Activation runbook](#activation-runbook) below. Related:
[`docs/architecture/video-render-vram-gate.md`](../architecture/video-render-vram-gate.md).

## Why this exists

The Wan 2.2 TI2V-5B render loads ~24 GB onto the display GPU. Root-cause
investigation (2026-07-12) found ~8.6 GB permanently stuck in WSL2's
`vmwp`/`vmmemWSL` process — stale Docker GPU retention that **no container
restart returns**. Only `wsl --shutdown` followed by a Docker Desktop restart
frees it, and the containerized worker runs _inside_ the WSL VM it would need
to reset, so this has to be a Windows host task, not something the pipeline
can do to itself.

The [VRAM gate](../architecture/video-render-vram-gate.md) and the
[reclaim path](../architecture/video-render-vram-gate.md#reclaim-pr-2-2026-07-12)
already stop this retention from ever causing a desktop freeze and claw back
the reclaimable ~7 GB from image-gen. This reset clears the remaining stubborn
~8.6 GB — but because it bounces the entire Docker stack (every container:
worker, brain, Postgres, image-gen, wan-server, Grafana, everything), it only
fires when the operator is demonstrably away and nothing is running.

## How it decides

Two scripts, split by what each is actually good at:

- **`scripts/idle-wsl-gpu-reset.ps1`** (host-native) — measures how long the
  user has been away via the Win32 `GetLastInputInfo` API, then hands that to
  the checker. If the checker says reset, it runs `wsl --shutdown`, restarts
  Docker Desktop, polls the worker's `/health` until it's back, and stamps the
  cooldown. `-DryRun` runs the full decision and logs it without acting.
- **`scripts/idle_wsl_gpu_reset_check.py`** (DB/Prometheus) — the single
  source of truth for every condition except idle time. Reuses the same
  DSN-resolution pattern as `scripts/image-gen-server.py` / `scripts/gpu-scraper.py`.

The host script runs the checker with a **concrete Python interpreter, not
`poetry run`** — from the main checkout `poetry run python` can silently
resolve the wrong interpreter (3.11, which crashes the checker), and a fresh
worktree has no poetry env at all. Resolution order (logged at the top of
every run as `Checker interpreter:`):

1. `$env:POINDEXTER_IDLE_RESET_PYTHON` — optional operator override, an
   absolute path to a `python.exe` (the escape hatch when neither default fits).
2. the repo-root `.venv\Scripts\python.exe` — portable and the same interpreter
   the sibling `gpu-scraper.py` host task already uses.
3. `poetry run python` — last resort; a wrong resolve here just makes the
   checker fail closed (no reset), never a bad reset.

All conditions must hold for a reset to fire:

| #   | Condition                                                       | Setting                                     | Default |
| --- | --------------------------------------------------------------- | ------------------------------------------- | ------- |
| 1   | Master switch on                                                | `idle_wsl_reset_enabled`                    | `false` |
| 2   | User idle at least this long                                    | `idle_wsl_reset_min_idle_minutes`           | `20`    |
| 3   | No `pipeline_tasks` in `in_progress`                            | (query, not a setting)                      | -       |
| 4   | No media dispatch in the last N minutes                         | `idle_wsl_reset_inflight_grace_minutes`     | `15`    |
| 5   | Render-GPU free VRAM below this (retention actually a problem)  | `idle_wsl_reset_trigger_free_vram_gb`       | `28`    |
| 6   | GPU utilization below `gpu_busy_threshold_percent` (not gaming) | `gpu_busy_threshold_percent` (existing key) | `30`    |
| 7   | Last reset at least this long ago                               | `idle_wsl_reset_cooldown_hours`             | `6`     |

Unreadable Prometheus telemetry fails **closed** (no reset) — same
fail-closed posture as the VRAM gate itself.

Condition 5's trigger is **clamped up to the render floor**
(`media_render_min_free_vram_gb`, default `25`) whenever it is configured
lower. A trigger below the floor is a dead zone: renders defer for lack of
VRAM (free < floor) while the idle reset that would reclaim it never fires
(free never drops under the lower trigger). The default is `28` — comfortably
above the `25` floor (poindexter#881).

## Logs and notifications

Every run appends to `%USERPROFILE%\.poindexter\logs\idle-wsl-gpu-reset.log`
(idle time, every condition's value, the decision, and — on an actual reset —
how long the stack took to report healthy again). A routine-severity Discord
message goes out before a reset starts and after it completes (or times out),
posted directly via the `discord_ops_webhook_url` secret — see the `notify()`
docstring in `idle_wsl_gpu_reset_check.py` for why this bypasses the normal
`notify_operator()` path (that path needs app-lifespan singletons a bare host
script was never going to have wired).

## Activation runbook

**Do not skip steps or reorder them.** Each one is a deliberate checkpoint —
this script can bounce every container on the box.

1. **Register (disabled):**

   ```powershell
   .\scripts\register-idle-wsl-reset.ps1 -Install
   ```

   Creates the Scheduled Task in a disabled state. It will not fire yet.

2. **Dry-run against live state:**

   ```powershell
   .\scripts\idle-wsl-gpu-reset.ps1 -DryRun
   ```

   Review `%USERPROFILE%\.poindexter\logs\idle-wsl-gpu-reset.log` — check the
   idle-time reading looks right and the VRAM/utilization numbers match what
   Grafana's Hardware & Power dashboard shows. Run this a few times across
   different real conditions (mid-render, idle, actively working) before
   trusting the decision logic.

3. **Turn the master switch on:**

   ```
   poindexter settings set idle_wsl_reset_enabled true
   ```

   (or via the console). The Scheduled Task is still disabled at this point —
   this only arms the _decision logic_, not the schedule.

4. **One supervised live reset, Matt at the keyboard:**

   ```powershell
   .\scripts\idle-wsl-gpu-reset.ps1
   ```

   Run it manually (not via the Scheduled Task yet) while genuinely idle so
   the conditions pass for real. Watch it: `wsl --shutdown`, Docker Desktop
   restart, the health poll, the completion Discord message. Confirm the
   stack actually comes back — every container healthy, GPU free VRAM back
   near baseline (Hardware & Power dashboard).

5. **Only after step 4 succeeds, enable the schedule:**
   ```powershell
   .\scripts\register-idle-wsl-reset.ps1 -Enable
   ```
   Now it polls every 15 minutes and will reset unattended whenever every
   condition holds.

### Rolling back

```powershell
.\scripts\register-idle-wsl-reset.ps1 -Disable    # stop the schedule, keep the task
.\scripts\register-idle-wsl-reset.ps1 -Uninstall  # remove the task entirely
```

or set `idle_wsl_reset_enabled=false` to disarm the decision logic without
touching the Scheduled Task at all — the fastest single-setting kill switch.

## Operational notes

- **`Docker Desktop.exe` path is hardcoded** in `idle-wsl-gpu-reset.ps1`
  (`C:\Program Files\Docker\Docker\Docker Desktop.exe`, the default install
  location). Update the script if Docker Desktop lives elsewhere on this
  machine.
- **The poll interval is 15 minutes**, set in
  `register-idle-wsl-reset.ps1`'s `$PollIntervalMinutes`. Each poll is cheap
  (a DB query + three Prometheus reads) when the conditions aren't met.
- **Registering the task is independent of arming it.** The task can be
  installed and enabled while `idle_wsl_reset_enabled=false` sits as a no-op
  every 15 minutes — useful for confirming the schedule itself fires
  correctly (check `Get-ScheduledTaskInfo`'s `LastRunTime`) before trusting it
  with real resets.
