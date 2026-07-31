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

| File                          | Purpose                                                                                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backup-precious.sh`          | Pre-migration backup — precious DBs (`pg_dump`) + useful volumes (`tar`) + operator config. Runs from Git Bash on the source host while its stack is up.   |
| `ollama-vision.sh`            | UUID-pins a second-GPU Ollama instance on `:11435` (Vulkan off, never-unload). Backs `ollama-vision.service`.                                              |
| `run-session.sh`              | Runs one scheduled ops session with git-worktree isolation for the committing ones. Backs `poindexter-session@.service`.                                   |
| `install-session-timers.sh`   | Generates + enables the 7 `systemd` session timers (the Task-Scheduler replacement).                                                                       |
| `../demo-clips/bake-clips.sh` | Re-bakes the VHS demo-clip library in a throwaway container (poindexter#937). Defers when the box is already loaded. Backs `poindexter-demo-bake.service`. |
| `docker-watchdog.sh`          | Minimal stack liveness watchdog (bare-metal replacement for `docker-watchdog.ps1` — no `wsl --shutdown`).                                                  |

## Units (`infrastructure/systemd/`)

| Unit                                   | Purpose                                                                                                                                                                                                               |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `liquidctl-ocp.service`                | Applies single-rail +12V OCP on the Corsair HXi PSU at boot (re-applied each boot; `initialize` resets to multi-rail otherwise).                                                                                      |
| `ollama-primary.service`               | Primary Ollama on `:11434` (all GPUs).                                                                                                                                                                                |
| `ollama-vision.service`                | Second-GPU-pinned vision Ollama on `:11435` (runs `ollama-vision.sh`).                                                                                                                                                |
| `claude-telegram.service`              | Operator Claude Code + Telegram channel session (waits for worker health).                                                                                                                                            |
| `poindexter-session@.service`          | Template for the scheduled ops sessions (runs `run-session.sh`).                                                                                                                                                      |
| `poindexter-demo-bake.{service,timer}` | Weekly (Sun 04:30) re-bake of the demo-clip library. Runs on the HOST because the bake needs `seccomp=unconfined` for headless Chromium, and triggering it in-container would need the root-equivalent Docker socket. |

Host services bind `0.0.0.0` (not loopback) so containers can reach them via
`host.docker.internal` (`extra_hosts: host-gateway` on `docker-ce`); gate
external access with `ufw`.
