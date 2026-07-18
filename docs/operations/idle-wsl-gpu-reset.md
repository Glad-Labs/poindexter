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

### Where the checker runs (it is a container-side tool)

**The checker runs INSIDE the worker container, not on the host.** Both of its
runtime dependencies resolve only on the Docker network:

- `app_settings.gpu_metrics_prometheus_url` is `http://prometheus:9090` — a
  compose hostname the Windows host **cannot resolve at all**. The checker reads
  free-VRAM and GPU utilization from Prometheus, so invoked from the host it can
  only ever report _"Prometheus VRAM/utilization unreadable — failing closed"_.
- the DB is reached in-container at `postgres-local:5432`. From the host it needs
  the published `:5433`, which is itself prone to Docker Desktop port-forward
  wedges (observed hanging on **both** IPv4 and IPv6 and surviving a full
  `wsl --shutdown` + Docker Desktop restart).

So a host-invoked checker could never produce a real decision, independent of the
interpreter bug (#883) and the registrar bug (#882) — this was the root cause
under both. The host script therefore shells out with:

```
docker exec <container> python /opt/scripts/idle_wsl_gpu_reset_check.py …
```

where repo-root `scripts/` is already bind-mounted at `/opt/scripts` and the
image carries py3.13 + asyncpg + httpx. Container name defaults to
`poindexter-worker`, overridable via `$env:POINDEXTER_IDLE_RESET_CONTAINER`. The
Availability is probed **per call** (not cached), because the post-reset
`--stamp-cooldown` / `--notify` calls happen after `wsl --shutdown` tore the
container down and the stack came back. The probe **retries** (3 attempts, 2 s
apart) so a container that bounces for a second — a deploy, a healthcheck flap —
doesn't lose the run.

There is deliberately **no host-python fallback**. This script is Docker-dependent
by definition: its whole job is `wsl --shutdown` plus restarting Docker Desktop,
so if Docker is unavailable there is nothing to reset. A fallback also could not
work on a normal install (the Prometheus hostname above), and it actively masked
failures — it once logged `docker exec` and then silently ran `poetry run python`
because the container restarted in the one second between two probes
(poindexter#887). If the container is unavailable the checker returns a plain
JSON error and the script logs one clear reason and exits 2 **without
resetting**:

```
Checker output: {"should_reset": false, "error": "checker container 'X' is not running - cannot evaluate, no reset"}
Checker reported an error (failing closed): ...
```

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
