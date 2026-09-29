# Linux host-native deployment artifacts

Reference `systemd` units + helper scripts for running Poindexter host-native on
Linux (native `docker-ce` for the container tier, plus host-native Ollama,
scheduled ops sessions, and hardware setup). These are **inert on Windows /
Docker Desktop** — they are deployed on a Linux host, not before.

They ship as **generic templates**: units default to a `poindexter` service user
and `/home/poindexter/...` paths. Substitute your own login and paths (or create
a dedicated `poindexter` user). The `*.sh` scripts are already user-agnostic
(`$HOME`-relative), so only the `*.service` `User=` / `ExecStart` lines need
editing. Some units have an installer that renders those lines for you:
`install-session-timers.sh` (the ops sessions) and `install-deploy-sync.sh`
(deploy sync, docker watchdog and GPU scraper, plus a refresh of the connector's
and the recovery agent's units on a host that already has them).

## Scripts (`scripts/linux/`)

| File                          | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backup-precious.sh`          | Pre-migration backup — precious DBs (`pg_dump`) + useful volumes (`tar`) + operator config. Runs from Git Bash on the source host while its stack is up.                                                                                                                                                                                                                                                                                                                                                      |
| `ollama-vision.sh`            | UUID-pins a second-GPU Ollama instance on `:11435` (Vulkan off, never-unload). Backs `ollama-vision.service`.                                                                                                                                                                                                                                                                                                                                                                                                 |
| `ollama-primary.sh`           | UUID-pins the primary Ollama to GPU 0 (Vulkan off) and refuses to start unpinned. Backs `ollama-primary.service`. The pin is what makes `gpu_lock_scopes.llm_primary = [0]` TRUE rather than merely declared — unpin it and you must widen that scope back, or the lock believes a disjointness the hardware no longer enforces.                                                                                                                                                                              |
| `run-session.sh`              | Runs one scheduled ops session with git-worktree isolation for the committing ones. Backs `poindexter-session@.service`.                                                                                                                                                                                                                                                                                                                                                                                      |
| `install-session-timers.sh`   | Generates + enables the 7 `systemd` session timers (the Task-Scheduler replacement).                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `../demo-clips/bake-clips.sh` | Re-bakes the VHS demo-clip library in a throwaway container (poindexter#937). Defers when the box is already loaded. Backs `poindexter-demo-bake.service`.                                                                                                                                                                                                                                                                                                                                                    |
| `docker-watchdog.sh`          | Minimal stack liveness watchdog (bare-metal replacement for `docker-watchdog.ps1` — no `wsl --shutdown`). The unit runs the **deploy clone's** copy, so a merged fix runs after one deploy pass. It brings the stack up from the deploy clone too (a dev-checkout `up -d` recreates 5 containers every pass: relative bind mounts resolve to different absolute paths, so the config hash never matches) and confirms an unhealthy worker 3x before acting (the worker bounces ~36x/12h on ordinary deploys). |
| `deploy-sync-launcher.sh`     | The deploy-sync unit's entry point, installed as a **copy** at `~/.poindexter/deploy-sync/`. Every fire it runs the deploy clone's committed `deploy-checkout-sync.sh`, so merged driver code runs on the next fire. It keeps the last copy that completed a clean pass and runs that one instead when the merged copy fails `bash -n` or dies before syncing, so a broken merge can't brick the syncer. See [the deploy driver](#the-deploy-driver-runs-the-deploy-clone).                                   |
| `install-deploy-sync.sh`      | Installs the launcher, seeds its last-known-good copy from the driver the host runs today, and renders the deploy-sync and docker-watchdog units (`User=`, `ExecStart=`) and the GPU scraper's (`User=`, `WorkingDirectory=`, `ExecStart=`; a running scraper is restarted onto the clone). It also refreshes the connector's and the recovery agent's units, only where the host already has them. Re-run it after changing the launcher or one of those unit templates.                                     |
| `install-oomd.sh`             | Installs + enables `systemd-oomd` with the swap-kill policy from `infrastructure/systemd/oomd/`. Re-run after editing those files. See [host OOM protection](../../docs/operations/host-oom-protection.md).                                                                                                                                                                                                                                                                                                   |
| `poindexter-cli.sh`           | The host `poindexter` launcher. `install-host-cli.sh` symlinks `~/.local/bin/poindexter` to the **deploy clone's** copy, so the launcher, the sync logic and the CLI code all deploy themselves. Each call brings the CLI venv's dependencies current (`cli-venv-sync.sh --ensure`, a few ms when current), then execs `~/.poindexter/cli-venv/bin/poindexter`. It never falls back to another tree. See [the host CLI](#the-host-cli-runs-the-deploy-clone).                                                 |
| `cli-venv-sync.sh`            | Keeps `~/.poindexter/cli-venv` (editable-installed from the deploy clone) on the deploy clone's `poetry.lock`: fingerprint → `poetry sync` → verify it imports `poindexter` from the clone → stamp. `flock`-serialised; `--ensure` / `--force` / `--check` / `--status`. Called by the launcher and by `deploy-checkout-sync.sh` (step 10).                                                                                                                                                                   |
| `install-host-cli.sh`         | One-time: builds the CLI venv and points `~/.local/bin/poindexter` at the launcher, keeping any launcher it replaces as `poindexter.pre-host-cli-<ts>`. Then proves `poindexter` imports from the deploy clone.                                                                                                                                                                                                                                                                                               |

## Units (`infrastructure/systemd/`)

| Unit                                         | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `liquidctl-ocp.service`                      | Applies single-rail +12V OCP on the Corsair HXi PSU at boot (re-applied each boot; `initialize` resets to multi-rail otherwise).                                                                                                                                                                                                                                                                                                                           |
| `ollama-primary.service`                     | Primary Ollama on `:11434`, **pinned to GPU 0** (runs `ollama-primary.sh`).                                                                                                                                                                                                                                                                                                                                                                                |
| `ollama-vision.service`                      | Second-GPU-pinned vision Ollama on `:11435` (runs `ollama-vision.sh`).                                                                                                                                                                                                                                                                                                                                                                                     |
| `claude-telegram.service`                    | Operator Claude Code + Telegram channel session (waits for worker health).                                                                                                                                                                                                                                                                                                                                                                                 |
| `poindexter-session@.service`                | Template for the scheduled ops sessions (runs `run-session.sh`).                                                                                                                                                                                                                                                                                                                                                                                           |
| `poindexter-demo-bake.{service,timer}`       | Weekly (Sun 04:30) re-bake of the demo-clip library. Runs on the HOST because the bake needs `seccomp=unconfined` for headless Chromium, and triggering it in-container would need the root-equivalent Docker socket.                                                                                                                                                                                                                                      |
| `poindexter-deploy-sync.{service,timer}`     | 10-min deploy: fetch/reset the dedicated deploy clone to `origin/main`, rebuild/bounce the containers whose code changed, `uv sync` + restart the mcp-http connector on `mcp-server/**` merges, restart the host daemons below when a file they load at start changed, and keep the host CLI's venv on the deployed `poetry.lock` (`cli-venv-sync.sh`). Runs the installed launcher, which runs the deploy clone's driver with a last-known-good fallback. |
| `poindexter-docker-watchdog.{service,timer}` | 5-min stack liveness watchdog (runs `docker-watchdog.sh` from the deploy clone).                                                                                                                                                                                                                                                                                                                                                                           |
| `poindexter-mcp-http.service`                | claude.ai-connector MCP HTTP server (:8004) — runs `mcp-server/http_server.py` **from the deploy clone**, venv at `mcp-server/.venv` inside the clone; kept current + restarted by the deploy-sync pass. `install-deploy-sync.sh` refreshes its template where the host has the unit; host-specific values go in a drop-in.                                                                                                                                |
| `poindexter-gpu-scraper.service`             | Writes `gpu_metrics` from the nvidia-smi exporter every 60s — runs `scripts/gpu-scraper.py` **from the deploy clone**; the deploy-sync pass restarts it when a file it loads at start changes (step 8b).                                                                                                                                                                                                                                                   |
| `poindexter-recovery-agent.service`          | Host Recovery Agent (:9841) the brain calls for host-level restarts — runs **from the deploy clone**; the deploy-sync pass restarts it when `scripts/recovery-agent.py` changes, never mid-action (step 8b). `install-deploy-sync.sh` refreshes its template where the host has the unit.                                                                                                                                                                  |

Host services bind `0.0.0.0` (not loopback) so containers can reach them via
`host.docker.internal` (`extra_hosts: host-gateway` on `docker-ce`); gate
external access with `ufw`.

### The host CLI runs the deploy clone

`poindexter` on the host used to run out of a poetry venv editable-installed
against the **working checkout**, so in-process commands (`media
approve/reject`, `settings`, `tasks`, …) ran whatever that tree held. On
2026-09-28 it was 148 commits behind `main`, because nothing advances a
checkout with uncommitted edits. Since Glad-Labs/glad-labs-stack#4156 the
command is a symlink to the deploy clone's `poindexter-cli.sh`. It runs
`~/.poindexter/cli-venv`, whose package is editable-installed from the deploy
clone, and keeps that venv's dependencies on the clone's lockfile. Install
once:

```bash
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-host-cli.sh
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/cli-venv-sync.sh --status
```

Design, failure handling and the alternatives that were rejected:
[ci-deploy-chain.md](../../docs/operations/ci-deploy-chain.md#the-host-cli-is-a-fifth-surface-and-it-runs-the-deploy-clone).

### The deploy driver runs the deploy clone

`poindexter-deploy-sync.service` used to run `deploy-checkout-sync.sh` out of
the operator's working checkout. Only `run-session.sh`'s ff-only pre-flight ever
advanced that tree, and it skips a dirty one, so on 2026-09-28 it sat 148 commits
behind, with four merged driver fixes (two of them render-kill guards) not
running. Since Glad-Labs/glad-labs-stack#4172 the unit runs an installed
launcher. Every fire, the launcher runs the deploy clone's committed driver. It
keeps the last copy that completed a clean pass and runs that one in the same
fire when the merged copy fails `bash -n`, or dies or hangs before it has moved
the clone to origin/main. That copy fetches and resets onto the fix. A deferral
or a failure after the clone moved never falls back. The status file and
heartbeat carry `driver` (`merged` / `last-known-good` / `direct`), and the brain
raises `deploy_sync_driver_fallback` when a pass ran the fallback. Install once,
and again after changing the launcher or one of its unit templates:

```bash
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-deploy-sync.sh
bash ~/.poindexter/deploy-sync/deploy-sync-launcher.sh --report
```

The same installer refreshes `poindexter-mcp-http.service` and
`poindexter-recovery-agent.service` when the host already has them (stack#4232).
It re-renders `User=`, `WorkingDirectory=` and `ExecStart=` onto the deploy clone
and `try-restart`s a unit only when a non-comment line changed, so a comment-only
template edit reaches the host without bouncing the connector or interrupting the
agent. It never installs or enables either one: each needs setup of its own (a uv
venv; a bootstrap token and a sudoers grant). Everything else in the installed file
is replaced by the template's, so a value that is specific to one host belongs in a
drop-in (`sudo systemctl edit <unit>`), which the installer never touches.

Design, the rules and what is not covered:
[ci-deploy-chain.md](../../docs/operations/ci-deploy-chain.md#the-deploy-driver-runs-the-deploy-clone-with-a-last-known-good-fallback).

### The deploy path watches itself (poindexter#977)

`deploy-checkout-sync.sh` writes a `deploy_sync_run` heartbeat into
`audit_log` on every pass, and `poindexter/brain/deploy_sync_probe.py` reads it each
brain cycle. Two conditions, deliberately separated:

| Condition                                                      | Finding               | Severity   |
| -------------------------------------------------------------- | --------------------- | ---------- |
| newest heartbeat older than `deploy_sync_max_age_minutes` (35) | `deploy_sync_stale`   | `critical` |
| last `deploy_sync_error_streak_threshold` (3) runs all errored | `deploy_sync_failing` | `warning`  |

The first means the deploy path stopped **running** — merged `main` is
silently not shipping, and nothing else reports it because a sync that does
not run emits nothing at all. The second means it is running and cannot
finish, which the timer will keep retrying on its own, so it informs rather
than pages. A `deferred-active-flow` pass (the sync waiting out an in-flight
render instead of restarting a busy worker) counts as **healthy** liveness —
treating deferral as an error would page every busy evening.

**Don't fast-forward the deploy clone by hand. Run
`systemctl start poindexter-deploy-sync.service` instead.** A hand
`git merge --ff-only` leaves the clone "already current" while the containers
are still on the old tree. Until poindexter#1068 that path skipped the busy
check entirely and bounced the worker straight through a live media render. The
same `wait_for_gap_or_defer` guard now holds that path too, but the service
run is still the route with the right logging and status.

The heartbeat goes to the DB rather than being read from
`~/.poindexter/deploy-checkout-sync.status.json`, because the brain container
mounts only subdirectories of `~/.poindexter`; exposing the root to read one
JSON file would also hand `bootstrap.toml` — the master key — to a container
with no need for it, and a single-file bind mount goes stale when the writer
replaces the inode.

> **The timer schedules on the clock, not on the last run.** It was
> `OnUnitActiveSec=10min`, which chains the next fire off the last
> activation. On 2026-08-02 host DNS dropped, the service failed repeatedly
> (correctly), and after one of those failures the timer stopped scheduling
> altogether (`NEXT: -`) — merged `main` sat undeployed for ~45 minutes and
> recovery needed a manual `systemctl start`. It is now `OnCalendar=*:0/10`
> with `Persistent=true`, so the next fire cannot depend on whether the last
> pass succeeded. Deliberately _not_ `Restart=on-failure`: during an outage
> that hammers a failing `git fetch`, and the next tick already provides the
> retry.

Applying a change to the unit or timer needs a re-render plus a reload.
Editing the repo file alone does nothing to a host that already has it
installed, and the installer does both:

```bash
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-deploy-sync.sh --no-start
systemctl list-timers poindexter-deploy-sync.timer   # NEXT must not be "-"
```
