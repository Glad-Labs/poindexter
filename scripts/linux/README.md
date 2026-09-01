# Linux host-native deployment artifacts

Reference `systemd` units + helper scripts for running Poindexter host-native on
Linux (native `docker-ce` for the container tier, plus host-native Ollama,
scheduled ops sessions, and hardware setup). These are **inert on Windows /
Docker Desktop** — they are deployed on a Linux host, not before.

They ship as **generic templates**: units default to a `poindexter` service user
and `/home/poindexter/glad-labs-website` checkout. Substitute your own login and
checkout path (or create a dedicated `poindexter` user). The `*.sh` scripts are
already user-agnostic (`$HOME`-relative), so only the `*.service` `User=` /
`ExecStart` lines need editing.

## Scripts (`scripts/linux/`)

| File                          | Purpose                                                                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backup-precious.sh`          | Pre-migration backup — precious DBs (`pg_dump`) + useful volumes (`tar`) + operator config. Runs from Git Bash on the source host while its stack is up.                                                                                                                                                                                                                                                             |
| `ollama-vision.sh`            | UUID-pins a second-GPU Ollama instance on `:11435` (Vulkan off, never-unload). Backs `ollama-vision.service`.                                                                                                                                                                                                                                                                                                        |
| `ollama-primary.sh`           | UUID-pins the primary Ollama to GPU 0 (Vulkan off) and refuses to start unpinned. Backs `ollama-primary.service`. The pin is what makes `gpu_lock_scopes.llm_primary = [0]` TRUE rather than merely declared — unpin it and you must widen that scope back, or the lock believes a disjointness the hardware no longer enforces.                                                                                     |
| `run-session.sh`              | Runs one scheduled ops session with git-worktree isolation for the committing ones. Backs `poindexter-session@.service`.                                                                                                                                                                                                                                                                                             |
| `install-session-timers.sh`   | Generates + enables the 7 `systemd` session timers (the Task-Scheduler replacement).                                                                                                                                                                                                                                                                                                                                 |
| `../demo-clips/bake-clips.sh` | Re-bakes the VHS demo-clip library in a throwaway container (poindexter#937). Defers when the box is already loaded. Backs `poindexter-demo-bake.service`.                                                                                                                                                                                                                                                           |
| `docker-watchdog.sh`          | Minimal stack liveness watchdog (bare-metal replacement for `docker-watchdog.ps1` — no `wsl --shutdown`). Brings the stack up from the **deploy clone** (a dev-checkout `up -d` recreates 5 containers every pass — relative bind mounts resolve to different absolute paths, so the config hash never matches) and confirms an unhealthy worker 3x before acting (the worker bounces ~36x/12h on ordinary deploys). |
| `install-oomd.sh`             | Installs + enables `systemd-oomd` with the swap-kill policy from `infrastructure/systemd/oomd/`. Re-run after editing those files. See [host OOM protection](../../docs/operations/host-oom-protection.md).                                                                                                                                                                                                          |

## Units (`infrastructure/systemd/`)

| Unit                                     | Purpose                                                                                                                                                                                                                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `liquidctl-ocp.service`                  | Applies single-rail +12V OCP on the Corsair HXi PSU at boot (re-applied each boot; `initialize` resets to multi-rail otherwise).                                                                                                                                                                                         |
| `ollama-primary.service`                 | Primary Ollama on `:11434`, **pinned to GPU 0** (runs `ollama-primary.sh`).                                                                                                                                                                                                                                              |
| `ollama-vision.service`                  | Second-GPU-pinned vision Ollama on `:11435` (runs `ollama-vision.sh`).                                                                                                                                                                                                                                                   |
| `claude-telegram.service`                | Operator Claude Code + Telegram channel session (waits for worker health).                                                                                                                                                                                                                                               |
| `poindexter-session@.service`            | Template for the scheduled ops sessions (runs `run-session.sh`).                                                                                                                                                                                                                                                         |
| `poindexter-demo-bake.{service,timer}`   | Weekly (Sun 04:30) re-bake of the demo-clip library. Runs on the HOST because the bake needs `seccomp=unconfined` for headless Chromium, and triggering it in-container would need the root-equivalent Docker socket.                                                                                                    |
| `poindexter-deploy-sync.{service,timer}` | 10-min deploy: fetch/reset the dedicated deploy clone to `origin/main`, rebuild/bounce the containers whose code changed, and `uv sync` + restart the mcp-http connector on `mcp-server/**` merges (runs `deploy-checkout-sync.sh` from the operator checkout — the clone is the reset target, never the script source). |
| `poindexter-mcp-http.service`            | claude.ai-connector MCP HTTP server (:8004) — runs `mcp-server/http_server.py` **from the deploy clone**, venv at `mcp-server/.venv` inside the clone; kept current + restarted by the deploy-sync pass.                                                                                                                 |

Host services bind `0.0.0.0` (not loopback) so containers can reach them via
`host.docker.internal` (`extra_hosts: host-gateway` on `docker-ce`); gate
external access with `ufw`.

### The deploy path watches itself (poindexter#977)

`deploy-checkout-sync.sh` writes a `deploy_sync_run` heartbeat into
`audit_log` on every pass, and `brain/deploy_sync_probe.py` reads it each
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

Applying a change to the unit needs a re-copy plus a reload — editing the
repo file alone does nothing to a host that already has it installed:

```bash
sudo cp infrastructure/systemd/poindexter-deploy-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart poindexter-deploy-sync.timer
systemctl list-timers poindexter-deploy-sync.timer   # NEXT must not be "-"
```
