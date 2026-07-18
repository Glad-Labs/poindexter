# Design: Windows 11 → Pop!\_OS full-wipe migration of Matt's workstation

- **Date:** 2026-07-17
- **Status:** ✅ Design approved (Matt, 2026-07-17) — pending spec review, then implementation plan
- **Author:** Claude (with Matt)
- **Area:** Host infrastructure (OS, Docker, GPU, orchestration, hardware config)
- **Umbrella issue:** [Glad-Labs/glad-labs-stack#295](https://github.com/Glad-Labs/glad-labs-stack/issues/295) — "bare-metal Linux for Matt's workstation." This spec **is** #295, executed as a full wipe.
- **Supersedes (as the durable cure):** `docs/superpowers/specs/2026-07-10-docker-ce-wsl2-migration-design.md` (the WSL-half-measure, NO-GO on networking). This spec reuses that doc's volume tiering + port inventory.
- **Visibility:** operator-internal — `docs/superpowers/` is stripped from the public poindexter mirror.

## Locked decisions (Matt, 2026-07-17)

1. **Full wipe** to Pop!\_OS (not dual-boot, not WSL docker-ce). Single OS on the box.
2. **Weekend big-bang** cutover — the backend pipeline is paused for the duration (~1–2 days). The public site (Vercel + R2) stays live throughout, so gladlabs.io never goes dark. No failover-to-the-5070-box leg.
3. **Backup = stage-on-drive-2 + offsite.** Back up precious data to the **MP600** (PCIe4) locally **and** push the irreplaceable bits to Backblaze/R2. Install Pop!\_OS on the **MP700** (PCIe5), restore locally from the MP600, then wipe the MP600 and mount it as Linux `/data`.
4. **Ollama stays host-native** — two `systemd` services, not containerized.

## Motivation — and honest scope

The trigger is the **WSL2/Docker-Desktop instability** (VRAM/system-RAM contention + the recurring wedge episodes). But the instability history splits into **three separate root causes**, and this migration fixes them to very different degrees. Stating this up front so the plan solves the right problem and nobody expects a fix-all.

| Problem class                         | What it is                                                                                                                  | Does bare-metal Pop!\_OS fix it?                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Docker Desktop / WSL2 wedge**    | The `localhost:5433` port-proxy wedge (~6× in ~3 weeks), the VHDX "everything down", the WSL-VM OOM from the cadvisor leak. | **Yes — eliminated.** No utility VM, no VHDX, no `com.docker.backend` proxy. A container that publishes `:5433` is on the host loopback. This class cannot occur. This is the primary win and maps to the stated trigger.                                                                                                                                                                                                                                                                                  |
| **2. Host RAM oversubscription**      | Kernel-Power 41 freezes: ~42 containers + a 2-GPU inference fleet + daily desktop on 64 GB, WSL pinning a 32 GB balloon.    | **Helped, not eliminated.** Containers share the host's 64 GB directly through the page cache — no VM double-booking, no balloon tax (reclaims ~10–20 GB effective headroom). Does **not** add RAM; if load stays, 64 GB is still tight and 128 GB remains on the table.                                                                                                                                                                                                                                   |
| **3. PSU multi-rail OCP false-trips** | The "instantly dark & silent" resets. Diagnosed 2026-07-13; fixed by switching the HX1500i to **single-rail OCP in iCUE**.  | **OS-independent — neither caused nor fixed by Linux, and NOT stranded by the wipe.** The HX1500i stores the single/multi-rail toggle in its own **onboard memory**, so the 2026-07-13 iCUE fix persists through the wipe, without iCUE, and even across machines (confirmed 2026-07-18 by Matt, per the Corsair HXi-2025 forum thread). Losing iCUE costs nothing here. The only way to lose it on Linux is a **bare `liquidctl initialize`**, which resets to multi-rail by default — see Hardware gate. |

The migration also delivers a real **cleanup payoff**: a whole layer of WSL-specific self-healing scaffolding becomes obsolete and gets retired (see "What we retire").

## Current-state inventory (source of truth for the plan)

**Host / GPU**

- Windows 11 Pro, Docker Desktop on the WSL2 backend.
- GPUs (confirmed via `nvidia-smi` 2026-07-17): **GPU0 = RTX 5090 (32 GB)**, **GPU1 = RTX 3090 (24 GB)**, driver **610.62**.
- Drives: **MP700 2 TB PCIe5** (target OS drive) + **MP600 2 TB PCIe4** (backup-stage → `/data`).

**Container tier** — full operator stack (`docker-compose.local.yml`, ~42 containers). **18 published host ports to preserve identically** (from the 2026-07-10 spec):

```
8002 worker      3000 grafana      5433 postgres-local(→5432)
3010 langfuse    8080 glitchtip    9091 prometheus(→9090)
9093 alertmanager 3100 loki        3200 tempo(+4317-4318 OTLP)
4200 prefect     4040 pyroscope    3002 uptime-kuma(→3001)
5003 postiz(→5000) 18443 pgadmin(→80) 8001 speaches(→8000)
9836 image-gen   9840 wan-server   7880-7882 livekit
```

Same ports post-cutover → `bootstrap.toml`'s `localhost:5433` DSN + all host tooling are **zero-touch**.

**Volume tiering** (from the 2026-07-10 spec — reused wholesale):

- **Precious** (`pg_dump`, ~2.5 GB logical): `poindexter_brain` (1.24 GB), `prefect` (1.2 GB), `langfuse` (14 MB), Postiz DB. **Skip** the ~15 junk `poindexter_unit_*/test/e2e/flatten_*` DBs.
- **Useful** (`tar`, best-effort): grafana, glitchtip-db, langfuse-clickhouse + minio, uptime-kuma, postiz-uploads.
- **Disposable** (start fresh): prometheus, loki, tempo, pyroscope, promtail-positions, alertmanager, caches.

**Host-native tier** (Task Scheduler + startup `.cmd` today → `systemd` after):

- Ollama ×2: primary `:11434` (GPU0/5090), vision `:11435` (GPU1/3090, `OLLAMA_KEEP_ALIVE=-1`, launched by `scripts/ollama-vision-gpu1.ps1`).
- Exporters: `windows_exporter`, the `nvidia-smi`/GPU scraper (Python).
- Claude Code + Telegram channel autostart (`claude-telegram.cmd`), recovery-agent, MCP-HTTP.
- Scheduled agents: `scripts/claude-sessions.ps1` dispatching `scripts/ops_sessions/*.py` (7 live: `dependency_review`, `codebase_audit`, `doc_sync`, `claude_md_sync`, `triage_sweep`, `alert_triage`, `test_health`; 2 disabled frontier sessions have no script).

**Orchestration scripts** (`scripts/`): mix of PowerShell (Windows) and bash (portable). Bash already present and reusable: `start-stack.sh`, `bootstrap.sh`, `brain-watchdog.sh`, `db-backup-local.sh`, `system-health-check.sh`, `sync-to-github.sh`, `push-everywhere.sh`. WSL-only PowerShell to **delete**: `idle-wsl-gpu-reset.ps1`, `register-idle-wsl-reset.ps1`, `fix-task-window-visibility.ps1`, and `docker-watchdog.ps1`'s `wsl --shutdown` recovery path.

**Hardware config (iCUE today)**

- HX1500i (2025) PSU — **single-rail OCP** (the 2026-07-13 crash fix).
- 16× iCUE LINK fans + XD5 pump + XC7 LCD CPU block via the iCUE LINK System Hub — fan curves + RGB.
- Monitoring: AIDA64, MSI Afterburner + RTSS, LibreHardwareMonitor.

## Target architecture

| Concern             | Windows today                           | Pop!\_OS after                                                              |
| ------------------- | --------------------------------------- | --------------------------------------------------------------------------- |
| OS                  | Windows 11 Pro                          | **Pop!\_OS 24.04 LTS (COSMIC Epoch 1)** on MP700                            |
| Second drive        | data / games                            | wiped → mounted `/data` (Ollama model cache, media, local backups)          |
| Container engine    | Docker Desktop (WSL2)                   | **native `docker-ce` + compose v2**                                         |
| Host↔container path | `com.docker.backend` proxy (**wedges**) | direct host loopback — **no proxy, wedge class impossible**                 |
| GPU → containers    | `/dev/dxg` (WSL2 GPU-PV)                | **`nvidia-container-toolkit`** on native driver                             |
| Ollama ×2           | Task Scheduler                          | **`systemd` services**, `CUDA_VISIBLE_DEVICES` 0/1, ports 11434/11435       |
| Orchestration       | Task Scheduler + startup `.cmd`         | **`systemd` services + timers**                                             |
| Host metrics        | `windows_exporter`                      | **`node_exporter`** (Prometheus scrape swap)                                |
| GPU metrics         | `nvidia-smi` scraper (Python)           | keep the Python scraper, or **`dcgm-exporter`**                             |
| PSU OCP             | iCUE single-rail                        | unchanged — persists in PSU onboard memory (optional `liquidctl` re-assert) |
| Fan curves          | iCUE                                    | **BIOS** (safety floor) + **CoolerControl**                                 |
| RGB + LINK sensors  | iCUE                                    | **OpenLinkHub**                                                             |
| Remote access       | Tailscale (`nightrider`)                | **Tailscale native** (same identity; mirrored/Tailscale conflict now moot)  |
| Operator identity   | `~/.claude`, `~/.poindexter`, repos     | restored from backup                                                        |

## Phased plan

Everything before Phase 3 is **non-destructive and abortable at zero cost** — Windows stays fully bootable. Phase 3 is the single point of no return.

### Phase 0 — Live-USB hardware validation (go/no-go gate, zero risk)

Boot Pop!\_OS 24.04 from a USB stick and prove, without touching either drive:

1. `nvidia-smi` lists **both** GPUs (5090 + 3090).
2. Display drives the Acer ultrawide at native 3440×1440.
3. Both NVMe drives + network (Ethernet/Wi-Fi) are visible.
4. _(informational, not a gate)_ `liquidctl list` sees the HX1500i and `liquidctl status` reports it — single-rail OCP already persists in PSU memory, so this only confirms Linux-side visibility/telemetry.
5. `OpenLinkHub` detects the iCUE LINK System Hub (fans + RGB).

**If a GPU or the PSU tool fails here → stop and rethink, Windows still 100% intact.** This gate is the whole safety story.

### Phase 1 — Backup + verify (non-destructive)

- `pg_dumpall --globals-only` (roles/passwords) + per-DB `pg_dump` of `poindexter_brain`, `prefect`, `langfuse`, Postiz. Skip the junk DBs.
- `tar` the "useful" volumes.
- Copy `~/.poindexter/` (incl. `bootstrap.toml` secrets), `~/.claude/` (memory, plugins, keybindings, settings), and local repo clones/worktrees.
- Write **both** to the MP600 **and** to Backblaze/R2. **Confirm the offsite copy covers the precious paths** (verify against the Backblaze backup config, don't assume).
- **Test-restore** the Postgres dumps into a throwaway container and row-count/extension-check (pgvector, pgcrypto) **before** Phase 3.
- Inventory any Windows-only apps/games worth noting before they're gone.

**Credential re-homing (unrecoverable after the wipe — a hard gate before Phase 3).** Windows Hello passkeys are TPM-bound and destroyed by the wipe, and most of Matt's 2FA is Hello-based. Re-home each onto the **Pixel 9** (passkeys sync to the Google account and roam to the Linux PC over Bluetooth/QR — nothing enrolls on the machine itself) **while Hello still works**:

- **Inventory:** Windows **Settings → Accounts → Passkeys** lists every passkey saved to this PC — screenshot it (the authoritative device-side list). Then sweep each critical account's security settings for a passkey/security key bound to this device.
- **Blast-radius order:** **Backblaze** (the offsite-backup target — a lockout here breaks recovery itself), **Mercury** (banking), **domain registrar** + **Cloudflare**, then **Google / GitHub / Vercel / Anthropic / Resend / HuggingFace**, then Discord / Telegram / X / LinkedIn / Bluesky / Reddit.
- **Per account, before the wipe:** add the Pixel as a passkey/authenticator, verify it signs in, and save **printed recovery codes** for the top-tier accounts. **Harden the Google account hardest** — it backs up every Pixel passkey, so its own recovery must not depend on the phone alone.
- **Gate:** no account may be left where the only surviving factor is a wiped Hello passkey — verified per-account **before** Phase 3.

### Phase 2 — Fan-safety pre-set (non-destructive, light)

The iCUE LINK hub + XD5 pump **persist their curves in onboard memory**, so they keep running their set profile with no live controller after the wipe (confirmed by Matt). This gate is therefore light: set a safe, aggressive fan curve in **motherboard BIOS** as a belt-and-suspenders OS-independent floor, and treat Linux fan control (CoolerControl/OpenLinkHub) as optional polish rather than a safety dependency.

### Phase 3 — ⛔ POINT OF NO RETURN: wipe MP700 + install Pop!\_OS

- Wipe MP700, install Pop!\_OS 24.04 (NVIDIA flavor).
- Both GPUs on a working driver (ships 585 → upgrade toward a 610-class driver via System76 packages / graphics-drivers PPA, aligned with `nvidia-container-toolkit`'s CUDA).
- _(optional hardening)_ install + enable the `liquidctl` OCP one-shot. The PSU already retains single-rail through the wipe; the unit is only insurance against a stray bare `liquidctl initialize`.

### Phase 4 — Restore + rebuild the stack

- Install `docker-ce` + compose v2 + `nvidia-container-toolkit`.
- Restore Postgres (globals then per-DB), `tar`-restore useful volumes.
- Bring the stack up via `start-stack.sh` on **identical ports**; `bootstrap.toml` unchanged, `localhost:5433` now resolves proxy-free.
- Install Ollama; create the two `systemd` services (GPU-pinned, 11434/11435); re-pull or copy models to `/data`.
- Tailscale up (`nightrider`); Claude Code + Telegram autostart.

### Phase 5 — Re-home orchestration

- `systemd` **timers** wrapping `ops_sessions/*.py` (replace Task Scheduler); DST-correct via `OnCalendar` in `operator_timezone`.
- `systemd` **services** for stack bring-up, Ollama ×2, the Claude+Telegram session, recovery-agent, node_exporter.
- Swap the Prometheus scrape target `windows_exporter` → `node_exporter`.
- **Delete** the obsolete WSL scripts (see below); simplify `docker-watchdog` to a `systemctl restart docker` liveness check.

### Phase 6 — Verify + repurpose MP600

- Full verification (below). Then wipe the MP600 and mount it as `/data`; move the Ollama model cache there.

## Hardware-safety gate (detail)

- **PSU OCP — already safe; the setting lives in the PSU.** The HX1500i saves the single/multi-rail toggle to onboard memory, so the 2026-07-13 fix survives the wipe, iCUE's absence, and even a move to another machine. **No Linux action is required.** The one hazard: liquidctl's `initialize` **resets to multi-rail by default**, so never run a bare `sudo liquidctl initialize` — pass `--single-12v-ocp` if you run it at all. The optional `liquidctl-ocp.service` one-shot re-asserts single-rail each boot purely as insurance against that.
- **Fans:** the iCUE LINK hub + XD5 pump retain their curves in onboard memory, so cooling keeps running the set profile with no live controller. BIOS curve is a belt-and-suspenders floor; CoolerControl adds richer curves on Linux but is optional, not a safety dependency.
- **RGB + LINK sensors:** OpenLinkHub (web dashboard). Cosmetic; may need device-claim coordination with CoolerControl/liquidctl (all three can want the same USB/hwmon devices — validate the coexistence in Phase 0/6, don't let them fight).

## What we retire (cleanup payoff)

Obsolete on bare metal — delete or gut during Phase 5:

- `docker-watchdog.ps1`'s `wsl --shutdown` recovery (no WSL VM to force-kill).
- The brain's `docker_port_forward_probe` DB-wedge self-heal + its `alert_events` routing (the wedge cannot occur).
- `idle-wsl-gpu-reset.ps1` + `register-idle-wsl-reset.ps1` (WSL GPU-PV reset).
- `fix-task-window-visibility.ps1` (Task Scheduler window-hiding — no windows on Linux).
- `.wslconfig` memory-balloon tuning, VHDX compaction runbooks, and the `#2239` autovacuum-stat-reset workaround (crash-restart from `wsl --shutdown` no longer happens).

## Risks & mitigations

| Risk                                                                      | Likelihood               | Mitigation                                                                                                                                                  |
| ------------------------------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5090 driver snags on Pop 24.04 (documented Blackwell install friction)    | Med                      | **Phase 0 live-USB proves it first**; Windows intact until Phase 3; driver upgrade path via System76 / graphics-drivers PPA                                 |
| PSU reverts to multi-rail → OCP crashes return                            | Low                      | Setting persists in the PSU's onboard memory through the wipe; only a bare `liquidctl initialize` resets it — optional boot one-shot re-asserts single-rail |
| GPUs thermally exposed before Linux fan tooling loads                     | Low                      | Fans + pump retain onboard curves (no live controller needed); BIOS floor as belt-and-suspenders                                                            |
| liquidctl doesn't recognize the 2025 HX1500i USB ID                       | Low — no longer material | Affects only optional Linux-side re-assert/telemetry; the OCP setting itself persists in the PSU regardless                                                 |
| Account lockout — a wiped Hello passkey was an account's only factor      | Med–High                 | **Phase 1 credential re-homing gate**: re-home to the Pixel + printed recovery codes for top-tier accounts, verified per-account before Phase 3             |
| Data loss on wipe                                                         | Low                      | Backup MP600 **+** offsite, **test-restore before Phase 3**                                                                                                 |
| Postgres restore drift (roles, pgvector/pgcrypto)                         | Med                      | `--globals-only` for roles; same image tag → same major; extension + row-count check pre-cutover                                                            |
| RAM still tight (problem #2 unchanged)                                    | Med                      | Reclaim WSL tax now; 128 GB upgrade stays on the table                                                                                                      |
| iCUE LINK tools (OpenLinkHub/CoolerControl/liquidctl) contend for devices | Low                      | Validate coexistence; assign ownership per device                                                                                                           |

## Rollback

Clean and free **up to Phase 3**: every prior step is non-destructive, Windows stays bootable, abort at zero cost. **After Phase 3**, rollback = reinstall Windows from the backup — which is exactly why Phase 0 (hardware proof) + a **test-restored** backup are the entire safety model. No parallel-run soak (single box, full wipe).

## Open decisions / deferred

- **Driver target version** on Pop!\_OS (585 stock vs upgrade to a 610-class) — resolve empirically in Phase 3 against `nvidia-container-toolkit` + Ollama CUDA needs.
- **GPU metrics:** keep the existing Python `nvidia-smi` scraper vs adopt `dcgm-exporter` — decide in Phase 5 (dashboards should need minimal change either way).
- **Incremental orchestration port:** only the minimal Linux orchestration set ships at cutover (stack bring-up, Ollama ×2, scheduled agents, Claude+Telegram, node_exporter, hardware one-shots). Remaining PowerShell utilities (`deploy-worker`, `kuma-backup`, `prune-mcp-orphans`, `voice-brain-host`, …) are ported **incrementally post-cutover**, not in the migration weekend. (YAGNI.)
- **128 GB RAM** upgrade — separate hardware decision, not gated by this migration.

## Success criteria / definition of done

1. Both GPUs enumerated by `nvidia-smi` and usable **in a container** (`docker run --gpus all … nvidia-smi`).
2. A content-pipeline task runs **end-to-end** on the graph_def path with GPU (writer on 5090, vision on 3090).
3. `localhost:5433` + host CLI (`poindexter tasks list`) + the `postgres` MCP all reach the DB — **no wedge, no workaround** — under connection churn.
4. Grafana, alerts, brain heartbeat, and self-heal all green; scheduled agents fire on their `systemd` timers.
5. PSU still reporting single-rail (persisted in PSU memory; spot-check `liquidctl status` if available); fans/pump running their onboard curves + BIOS floor active; temps sane under a dual-GPU load test.
6. Public site unaffected throughout (Vercel + R2), backend restored within the weekend window.
7. Obsolete WSL scaffolding removed; `bootstrap.toml`/DSN unchanged.
8. Every re-homed account signs in from the Linux box via the Pixel (passkey/authenticator); no account locked out; recovery codes stored for the top-tier set.

## References

- `docs/superpowers/specs/2026-07-10-docker-ce-wsl2-migration-design.md` — the WSL half-measure (NO-GO); volume tiering + port inventory reused here.
- [Glad-Labs/glad-labs-stack#295](https://github.com/Glad-Labs/glad-labs-stack/issues/295) — bare-metal Linux umbrella (this spec).
- liquidctl HXi/RMi PSU guide — `--single-12v-ocp`, kernel-driver caveat.
- OpenLinkHub — iCUE LINK System Hub on Linux.
- Memory: `project_vram_oversub_docker_crashes` (the three crash classes), `user_profile` (hardware build), `project_gladlabs_infra` (local-first architecture).
