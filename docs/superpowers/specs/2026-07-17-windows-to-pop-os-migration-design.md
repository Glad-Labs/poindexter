# Design: Windows 11 → Pop!\_OS migration, via a reversible dual-boot evaluation

- **Date:** 2026-07-17 · **Revised 2026-07-18** (strategy changed from full wipe → dual-boot; see "Why this changed")
- **Status:** ✅ Design approved (Matt) — Phase 1 backup executed + test-restore passed 2026-07-18
- **Author:** Claude (with Matt)
- **Area:** Host infrastructure (OS, Docker, GPU, orchestration, hardware config)
- **Umbrella issue:** [Glad-Labs/glad-labs-stack#295](https://github.com/Glad-Labs/glad-labs-stack/issues/295) — "bare-metal Linux for Matt's workstation." This spec **is** #295.
- **Supersedes (as the durable cure):** `docs/superpowers/specs/2026-07-10-docker-ce-wsl2-migration-design.md` (the WSL half-measure, NO-GO on networking). Volume tiering + port inventory reused from it.
- **Visibility:** operator-internal — `docs/superpowers/` is stripped from the public poindexter mirror.

## Why this changed (2026-07-18)

The original spec locked a **full wipe**. That had a structural flaw, and Matt caught it: **the question the migration exists to answer could only be answered after the irreversible step.**

Phase 0 (live USB) proves the hardware _enumerates_ — GPUs appear, display works, drives are seen. It cannot prove the thing actually in doubt: that bare-metal Linux kills the wedge class **under real workload over days**, and that the environment is livable day to day. Under a full wipe you find that out only once Windows is gone.

Dual-boot inverts it. The evaluation runs against the real stack, on the real hardware, with Windows still bootable. The wipe stops being the experiment and becomes the **conclusion** — taken only once the evidence is in.

The cost is near zero because the MP600 has **1.7 TB free**. This is not a cramped compromise; it is a spacious second OS drive that happens to also hold the backup.

**What this does not change:** the target architecture, the native-Linux gotchas, the retirement list, and the credential gate are all unchanged. Only the _sequencing and reversibility_ changed.

## Locked decisions

1. **Dual-boot first, wipe later.** Pop!\_OS 24.04 installs to the **MP600** alongside Windows on the MP700. Both OSes remain bootable. The full wipe is deferred behind an explicit exit criterion (below) and is no longer part of the initial work.
2. **Separate physical drives, separate ESPs, BIOS boot menu.** Pop gets its **own** EFI System Partition on the MP600. Nothing writes to the MP700's ESP. OS selection is the firmware boot menu (F8), not a shared bootloader — so neither OS can break the other's boot path.
3. **One owner of the database at a time** — see "Split-brain: the governing rule." Non-negotiable; this is the main new risk dual-boot introduces.
4. **Backup safety precedes partitioning.** The precious tier must exist on **two devices that are not the MP600** before the MP600 is repartitioned.
5. **Ollama stays host-native** — two `systemd` services, not containerized.
6. **The public site is never involved.** Vercel + R2 serve gladlabs.io throughout, in every phase, including the eventual wipe.

## Motivation — and honest scope

The trigger is the **WSL2/Docker-Desktop instability** (VRAM/system-RAM contention + recurring wedge episodes). The instability history splits into **three separate root causes**, fixed to very different degrees. Stated up front so the plan solves the right problem and nobody expects a fix-all.

| Problem class                         | What it is                                                                                                                  | Does bare-metal Pop!\_OS fix it?                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Docker Desktop / WSL2 wedge**    | The `localhost:5433` port-proxy wedge (~6× in ~3 weeks), the VHDX "everything down", the WSL-VM OOM from the cadvisor leak. | **Yes — eliminated.** No utility VM, no VHDX, no `com.docker.backend` proxy. A container publishing `:5433` is on the host loopback. This class cannot occur. Primary win; maps to the stated trigger. **This is precisely what the dual-boot period is designed to verify empirically.**                                                               |
| **2. Host RAM oversubscription**      | Kernel-Power 41 freezes: ~42 containers + a 2-GPU inference fleet + daily desktop on 64 GB, WSL pinning a 32 GB balloon.    | **Helped, not eliminated.** Containers share the host's 64 GB directly via page cache — no VM double-booking, no balloon tax (reclaims ~10–20 GB effective headroom). Does **not** add RAM; 128 GB remains on the table.                                                                                                                                |
| **3. PSU multi-rail OCP false-trips** | The "instantly dark & silent" resets. Diagnosed 2026-07-13; fixed by switching the HX1500i to **single-rail OCP in iCUE**.  | **OS-independent — neither caused nor fixed by Linux, and not stranded by anything here.** The HX1500i stores the toggle in its own **onboard memory**, so the fix persists across OSes, without iCUE, even across machines. The only way to lose it is a **bare `liquidctl initialize`**, which resets to multi-rail — always pass `--single-12v-ocp`. |

Dual-boot additionally makes class 1 **falsifiable**: run the real workload on Linux for a defined period and count wedge events. Under the wipe plan that measurement was only obtainable after the point of no return.

## Split-brain: the governing rule

This is the one genuinely new risk dual-boot introduces, and it is worse than it first sounds.

If the stack runs under Windows some days and Linux others, there are **two Postgres instances diverging with no merge path**. A post published on one side does not exist on the other; `pipeline_tasks`, `audit_log`, `embeddings` and `cost_logs` all fork. There is no reconciliation tool and building one would cost more than the migration.

**The rule: exactly one OS owns the database at any time, and the handoff is explicit.**

Concretely, the adopted model is **cut over, don't alternate**:

- At cutover, Windows takes a final quiesced dump and **stops being the owner**.
- Linux restores that dump and is the sole owner from then on.
- Windows remains bootable as a **rollback path and for non-stack work only** — it must not run the stack again unless a deliberate hand-back happens (final Linux dump → restore into Windows), which is the same ceremony in reverse.

Rollback therefore stays cheap (reboot, restore the retained final dump) without ever running two live copies.

**Enforcement, not just intent.** Trusting memory here is how split-brain happens at 1am. Mechanical guards:

- The stack must **not** auto-start on boot on the non-owning OS. On Windows, disable the Docker Desktop auto-start and the scheduled-agent tasks at cutover; on Linux, `systemd` units are only enabled once Linux is the owner.
- Record the owner in a file both sides can read — the NTFS backup partition is visible to both (`D:\migration-backup\OWNER.txt`). Write `linux` / `windows` plus a timestamp at each handoff.
- Treat an unexpected owner value as a stop-and-think signal, not something to overwrite.

## Exit criteria — deciding, rather than drifting

"Dual-boot until I'm certain" becomes permanent two-OS maintenance unless certainty is defined in advance, while still undecided. Both outcomes are explicit:

**Commit to Linux (→ wipe Windows) when _all_ hold over a ≥14-day evaluation:**

1. **Zero** `localhost:5433` wedge events and zero VHDX-class "everything down" incidents (the class the migration exists to kill).
2. The content pipeline runs end-to-end nightly on the graph_def path with GPU, at the same success rate as the Windows baseline.
3. Both GPUs usable in containers continuously; no driver regressions across a reboot cycle.
4. Scheduled agents fire reliably on `systemd` timers; brain heartbeat, Grafana and alerting all green.
5. No unresolved daily-driver blocker (something that makes the machine unpleasant to work on, not merely different).

**Revert to Windows when _any_ holds:**

1. A Linux-specific stability class appears that is worse than what it replaced.
2. A hard hardware/driver blocker with no tractable fix (e.g. a GPU unusable in containers).
3. 14 days elapse without criterion 2 above being met — i.e. the pipeline never reached parity.

**Deliberately excluded from the verdict: raw speed.** See the drive-speed confound below.

### The drive-speed confound (evaluation validity)

The MP600 CORE XT is **PCIe4 QLC**; the MP700 ELITE is **PCIe5**. Postgres and Docker do sustained writes, which is exactly where QLC is weakest. **Linux will likely feel slower, and that is the drive, not the OS.**

This is a real threat to the experiment's validity: judging Linux on the slow drive risks rejecting it for the wrong reason. Mitigation: judge on the **stability and correctness** criteria above; treat performance as informational only. If performance is the deciding factor, re-test with Linux on the MP700 before concluding — do not decide from the MP600 measurement.

## Current-state inventory (source of truth for the plan)

**Host / GPU**

- Windows 11 Pro, Docker Desktop on the WSL2 backend.
- GPUs (`nvidia-smi` 2026-07-17): **GPU0 = RTX 5090 (32 GB)**, **GPU1 = RTX 3090 (24 GB)**, driver **610.62**.
- Drives (confirmed 2026-07-18): **Disk 0 = MP700 2 TB PCIe5** (C:, Windows, 617 GB free) · **Disk 1 = MP600 CORE XT 2 TB PCIe4** (D:, 1706 GB free — the Pop!\_OS target) · plus two USB sticks (below).

**Backup tiers as they actually exist (verified 2026-07-18)** — the recovery picture is layered, and the layers are not equivalent:

| Location               | Form                       | Password needed to read?        | Notes                                                              |
| ---------------------- | -------------------------- | ------------------------------- | ------------------------------------------------------------------ |
| `D:\migration-backup`  | **plain** `pg_dump` + tars | **No**                          | 47.33 GB. Most directly recoverable artifact. Test-restore passed. |
| `F:\poindexter-backup` | restic repo (local tier)   | Yes — restic password           | 26.9 GB on a 117 GB USB, written hourly. **Do not repurpose F:.**  |
| Backblaze B2           | restic repo (offsite tier) | Yes — restic password + B2 keys | `poindexter_brain` only; verified current.                         |
| `E:` (SanDisk 29 GB)   | FAT32, appears unused      | —                               | The stick to flash for install/rescue media.                       |

Both restic tiers inherit the recovery-credential cycle tracked in [poindexter#889](https://github.com/Glad-Labs/poindexter/issues/889) — the credentials needed to open them live inside the database they contain. This is why the plain `D:` copy currently matters more than its tier would suggest, and why the credential gate below is load-bearing.

**Container tier** — full operator stack (`docker-compose.local.yml`, ~42 containers). **18 published host ports to preserve identically:**

```
8002 worker      3000 grafana      5433 postgres-local(→5432)
3010 langfuse    8080 glitchtip    9091 prometheus(→9090)
9093 alertmanager 3100 loki        3200 tempo(+4317-4318 OTLP)
4200 prefect     4040 pyroscope    3002 uptime-kuma(→3001)
5003 postiz(→5000) 18443 pgadmin(→80) 8001 speaches(→8000)
9836 image-gen   9840 wan-server   7880-7882 livekit
```

Same ports post-cutover → `bootstrap.toml`'s `localhost:5433` DSN + all host tooling are **zero-touch**.

**Volume tiering:**

- **Precious** (`pg_dump`): `poindexter_brain`, `prefect`, `langfuse`, plus `glitchtip` and `postiz` — the latter two live in **their own Postgres containers** with their own superusers (`poindexter-glitchtip-db`, `poindexter-postiz-db`). **Skip** the junk `*_unit_*`/`test`/`e2e`/`flatten_*` DBs.
- **Useful** (`tar`, best-effort): grafana, langfuse-clickhouse + minio, uptime-kuma, postiz-uploads, pgadmin. **Never tar a live Postgres data volume** — torn snapshot; those are dumped instead.
- **Disposable** (start fresh): prometheus, loki, tempo, pyroscope, promtail-positions, alertmanager, caches.

**Host-native tier** (Task Scheduler + startup `.cmd` today → `systemd` after):

- Ollama ×2: primary `:11434` (GPU0/5090), vision `:11435` (GPU1/3090, `OLLAMA_KEEP_ALIVE=-1`).
- Exporters: `windows_exporter`, the `nvidia-smi`/GPU scraper (Python).
- Claude Code + Telegram channel autostart, recovery-agent, MCP-HTTP.
- Scheduled agents: `scripts/claude-sessions.ps1` dispatching `scripts/ops_sessions/*.py` (7 live; 2 disabled frontier sessions have no script).

**Orchestration scripts** (`scripts/`): PowerShell + bash mix. Bash already reusable: `start-stack.sh`, `bootstrap.sh`, `brain-watchdog.sh`, `db-backup-local.sh`, `system-health-check.sh`, `sync-to-github.sh`, `push-everywhere.sh`. WSL-only PowerShell to **delete at the wipe** (not during dual-boot — Windows still needs them): `idle-wsl-gpu-reset.ps1`, `register-idle-wsl-reset.ps1`, `fix-task-window-visibility.ps1`, and `docker-watchdog.ps1`'s `wsl --shutdown` path.

**Hardware config (iCUE today)**

- HX1500i (2025) PSU — **single-rail OCP** (the 2026-07-13 crash fix), persisted in PSU onboard memory.
- 16× iCUE LINK fans + XD5 pump + XC7 LCD CPU block via the iCUE LINK System Hub.
- Monitoring: AIDA64, MSI Afterburner + RTSS, LibreHardwareMonitor.

## Target architecture

Unchanged from the original spec — dual-boot alters when we get here, not where.

| Concern             | Windows today                           | Pop!\_OS after                                                              |
| ------------------- | --------------------------------------- | --------------------------------------------------------------------------- |
| OS                  | Windows 11 Pro (MP700)                  | **Pop!\_OS 24.04 LTS (COSMIC)** — MP600 during evaluation                   |
| Container engine    | Docker Desktop (WSL2)                   | **native `docker-ce` + compose v2**                                         |
| Host↔container path | `com.docker.backend` proxy (**wedges**) | direct host loopback — **no proxy, wedge class impossible**                 |
| GPU → containers    | `/dev/dxg` (WSL2 GPU-PV)                | **`nvidia-container-toolkit`** on native driver                             |
| Ollama ×2           | Task Scheduler                          | **`systemd` services**, GPU-pinned by UUID, ports 11434/11435               |
| Orchestration       | Task Scheduler + startup `.cmd`         | **`systemd` services + timers**                                             |
| Host metrics        | `windows_exporter`                      | **`node_exporter`** (Prometheus scrape swap; `windows_*` → `node_*`)        |
| GPU metrics         | `nvidia-smi` scraper (Python)           | keep the Python scraper, or **`dcgm-exporter`**                             |
| PSU OCP             | iCUE single-rail                        | unchanged — persists in PSU onboard memory (optional `liquidctl` re-assert) |
| Fan curves          | iCUE                                    | **BIOS** (safety floor) + **CoolerControl**                                 |
| RGB + LINK sensors  | iCUE                                    | **OpenLinkHub**                                                             |
| Remote access       | Tailscale (`nightrider`)                | **Tailscale native** (same identity; mirrored/Tailscale conflict now moot)  |

### Native-Linux gotchas (load-bearing, non-obvious)

- `host.docker.internal` is **not** automatic on `docker-ce` — services needing it require `extra_hosts: ["host.docker.internal:host-gateway"]`.
- Host services containers must reach (Ollama, node_exporter) must bind **`0.0.0.0`**, not `127.0.0.1` — a loopback bind is unreachable from the docker bridge. Gate external exposure with `ufw`.
- Pin GPUs by **UUID** with `CUDA_DEVICE_ORDER=PCI_BUS_ID`; bare numeric indices are unreliable across reboots.

### Dual-boot-specific gotchas

- **Disable Windows Fast Startup before mounting NTFS from Linux.** Fast Startup leaves the filesystem in a hibernated, dirty state; mounting it read-write from Linux can corrupt it. This matters because the backup lives on an NTFS partition Linux will read.
- **RTC disagreement.** Windows treats the hardware clock as local time, Linux as UTC — each will skew the other's clock across reboots. Since `operator_timezone` drives cron fire-times, fix it deliberately: set Linux `timedatectl set-local-rtc 0` and configure Windows to treat the RTC as UTC, or accept and correct on each boot. Do not leave it unmanaged.
- **NTFS as the migration channel.** WSL2's Docker volumes live inside a VHDX (ext4 in a VM disk) and are **not** directly readable from Linux — data must move via dumps, not file copies. But the `D:` NTFS partition _is_ readable from Linux, so `D:\migration-backup` is the natural handoff channel. No USB shuttle needed.
- **Installer choice matters.** Pop's "Clean Install" takes the whole selected drive and may adopt the existing ESP. Use **Custom (Advanced)** partitioning to create a dedicated ESP on the MP600, so the MP700 is never written to.
- **Secure Boot must be disabled, and stays disabled.** Pop!\_OS ships no Secure-Boot-signed shim, and its current build (24.04 NVIDIA build 26, verified 2026-07-18) carries a bootloader on Microsoft's DBX revocation list from the BootHole CVE family — Rufus flags this at flash time. There is no newer ISO that fixes it, so this is a property of the distro, not a stale download. Windows 11 continues to boot without Secure Boot (it gates _installation_, not runtime), but this is a standing posture change for the whole dual-boot period, and re-enabling it after committing to Linux is a MOK-enrollment exercise. **Check BitLocker before changing the setting** — the Secure Boot change shifts the TPM's PCR measurements, which BitLocker reads as tampering and answers with a 48-digit recovery-key prompt. Verified `Off` on this machine 2026-07-19, so no suspend was needed; do not assume that holds on a rebuild.
- **Boot the installer via the firmware's `UEFI:` entry.** Most boards list a USB stick twice — UEFI and legacy/CSM. A legacy boot silently produces a legacy install that cannot cleanly dual-boot against a UEFI Windows, and the failure does not surface until much later. Confirm inside the live session with `ls /sys/firmware/efi` before trusting anything else.

## Phased plan

Phases 0–2 are unchanged and non-destructive. Phase 3 is no longer a point of no return: it **adds** an OS without removing one. The destructive step now lives at the very end, behind the exit criteria.

### Phase 0 — Live-USB hardware validation (go/no-go, zero risk)

Boot Pop!\_OS 24.04 from the USB and prove, without touching either drive: both GPUs enumerate; display at native 3440×1440; both NVMe drives + network visible. _(Informational, not a gate:)_ `liquidctl list` sees the HX1500i; OpenLinkHub detects the LINK hub.

**A GPU failure here → stop and rethink, Windows 100% intact.**

### Phase 1 — Backup + credential re-homing (non-destructive) — _partially complete_

**✅ Done 2026-07-18:** staged backup to `D:\migration-backup` (47.33 GB) and **test-restore passed** — `pg_restore` rc=0, `vector 0.8.2` + `pgcrypto` present, a 768-dim KNN query returned neighbours, 6/7 tables exact (the 7th was post-snapshot growth on an append-only table, confirmed, not loss).

**Still open, and both gate Phase 3:**

- **Offsite push** of the tiers the nightly job does not cover — `bootstrap.toml`, `~/.claude`, the four non-brain databases, and the volumes.
- **Credential re-homing.** Windows Hello passkeys are TPM-bound and destroyed by the eventual wipe. Re-home each onto the **Pixel 9** (passkeys sync to the Google account and roam to the Linux box over Bluetooth/QR — nothing enrolls on the machine) **while Hello still works**. Blast-radius order: **Backblaze** (lockout here breaks recovery itself), **Mercury**, **domain registrar** + **Cloudflare**, then Google / GitHub / Vercel / Anthropic / Resend / HuggingFace, then the social set. Harden the Google account hardest — it backs up every Pixel passkey. **Gate:** no account may be left where the only surviving factor is a wiped Hello passkey.
- **Break the offsite recovery cycle** ([poindexter#889](https://github.com/Glad-Labs/poindexter/issues/889)): store the restic password, both B2 keys, and `poindexter_secret_key` outside both the machine and the bucket, then **prove** recovery by listing a snapshot from an unrelated machine.

> **Dual-boot lowers the stakes here but does not remove the gate.** With Windows retained, a botched restore is recoverable by rebooting. The credential and offsite work still matters because it protects against hardware loss, which dual-boot does nothing about.

### Phase 2 — BIOS fan-safety floor (non-destructive, light)

The iCUE LINK hub + XD5 pump **persist curves in onboard memory** and keep running with no live controller. Set a safe aggressive curve in motherboard BIOS as an OS-independent floor; treat Linux fan control (CoolerControl/OpenLinkHub) as optional polish, not a safety dependency.

### Phase 3 — Partition the MP600 + install Pop!\_OS alongside Windows

**No longer destructive to Windows.** The only data at risk is on the MP600, which is why decision 4 (backup safety precedes partitioning) exists.

1. **Satisfy the two-device rule first.** The precious tier is small (~428 MB of dumps + ~2 GB config) — copy it to the MP700 **and** offsite before touching MP600 partitions. The MP600 must not hold the only copy of anything when it is repartitioned.
2. **Disable Windows Fast Startup** (required before Linux mounts NTFS).
3. **Shrink the NTFS `D:` partition** from Windows Disk Management, leaving comfortable room for the existing ~156 GB of data plus the backup, and freeing ~1.2 TB.
4. **Install Pop!\_OS via Custom (Advanced) partitioning** into the freed space: a **new ESP on the MP600**, root, and swap. Confirm the installer targets the MP600's ESP and never the MP700's.
5. **Verify both OSes boot** from the firmware boot menu before proceeding. This is the gate for Phase 3.
6. Bring the driver to a recent build (Pop ships ~585 → 610-class), aligned with `nvidia-container-toolkit`'s CUDA.
7. _(optional)_ install the `liquidctl` OCP one-shot as insurance against a stray bare `initialize`.

### Phase 4 — Build the stack on Linux + take ownership of the data

- Install `docker-ce` + compose v2 + `nvidia-container-toolkit`; verify `docker run --gpus all … nvidia-smi`.
- Clone the repo; restore `~/.poindexter` + `~/.claude` from the NTFS backup partition (directly readable — no USB shuttle).
- **The handoff ceremony (split-brain prevention):** quiesce the Windows stack, stop the host-side writers, prove the database is quiet, take the **final** dump, then restore it into Linux Postgres and write `OWNER.txt = linux`. Disable Windows stack auto-start.
- Bring the stack up on **identical ports**; `bootstrap.toml` unchanged, `localhost:5433` now proxy-free.
- Install Ollama; two GPU-pinned `systemd` services; models on the Linux side.
- Tailscale up (`nightrider`); Claude Code + Telegram autostart.

### Phase 5 — Re-home orchestration

- `systemd` **timers** wrapping `ops_sessions/*.py`; DST-correct via `OnCalendar` in `operator_timezone`.
- `systemd` **services** for stack bring-up, Ollama ×2, the Claude+Telegram session, recovery-agent, node_exporter.
- Swap the Prometheus scrape target `windows_exporter` → `node_exporter`, renaming `windows_*` → `node_*` in alert rules.
- Simplify `docker-watchdog` to a `systemctl restart docker` liveness check (no `wsl --shutdown` path).

**Do not delete the Windows-side scripts yet** — Windows is still the rollback path. Deletion happens at Phase 7.

### Phase 6 — Evaluation period (≥14 days)

Run the business on Linux. Measure against the exit criteria. Windows stays bootable and idle; the stack does not run there.

Rollback at any point: reboot to Windows, restore the retained final dump, flip `OWNER.txt`. Cost is a reboot plus a restore, not a reinstall.

### Phase 7 — ⛔ The wipe (only after the exit criteria are met)

Now, and only now, the original irreversible step — with the evidence already in hand:

- Wipe the MP700 and decide its role (see open decisions).
- Retire the obsolete WSL scaffolding (list below).
- Confirm no Windows-only dependency remains unported.

## Hardware-safety gate (detail)

- **PSU OCP — already safe; the setting lives in the PSU.** The HX1500i saves the single/multi-rail toggle to onboard memory, so the 2026-07-13 fix survives OS changes, iCUE's absence, and even a move to another machine. **No Linux action required.** The one hazard: liquidctl's `initialize` **resets to multi-rail by default** — never run a bare `sudo liquidctl initialize`; pass `--single-12v-ocp`. The optional `liquidctl-ocp.service` re-asserts single-rail each boot purely as insurance against that.
- **Fans:** the LINK hub + XD5 pump retain curves in onboard memory, so cooling runs its profile with no live controller. BIOS curve is a belt-and-suspenders floor; CoolerControl is optional polish.
- **RGB + LINK sensors:** OpenLinkHub. Cosmetic; may contend with CoolerControl/liquidctl for the same USB/hwmon devices — assign ownership per device rather than letting all three claim them.

## What we retire (cleanup payoff — at Phase 7, not before)

Obsolete once Windows is gone:

- `docker-watchdog.ps1`'s `wsl --shutdown` recovery (no WSL VM to force-kill).
- The brain's `docker_port_forward_probe` DB-wedge self-heal + its `alert_events` routing (the wedge cannot occur).
- `idle-wsl-gpu-reset.ps1` + `register-idle-wsl-reset.ps1` (WSL GPU-PV reset).
- `fix-task-window-visibility.ps1` (no windows on Linux).
- `.wslconfig` balloon tuning, VHDX compaction runbooks, and the `#2239` autovacuum-stat-reset workaround.

## Risks & mitigations

| Risk                                                                       | Likelihood | Mitigation                                                                                                                                                             |
| -------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Split-brain — two diverging databases**                                  | **Med**    | **The governing rule**: one owner at a time, cut over rather than alternate. Enforced mechanically — no stack auto-start on the non-owner, `OWNER.txt` handoff marker. |
| **Evaluation rejects Linux for drive speed, not OS quality**               | **Med**    | Verdict rests on stability/correctness criteria; performance is informational. If perf decides it, re-test on the MP700 before concluding.                             |
| **MP600 repartition damages the backup it holds**                          | Low        | Two-device rule: precious tier on the MP700 + offsite **before** partitioning. Windows Disk Management shrink is non-destructive but not risk-free.                    |
| **NTFS corruption from Fast Startup + Linux mount**                        | Med        | Disable Fast Startup before the first Linux NTFS mount (Phase 3 step 2).                                                                                               |
| **Installer writes to the MP700's ESP, disturbing Windows boot**           | Low–Med    | Custom (Advanced) partitioning with an explicit MP600 ESP; verify both OSes boot before proceeding.                                                                    |
| **Evaluation drifts indefinitely — permanent two-OS maintenance**          | Med        | Exit criteria fixed in advance, with a 14-day revert trigger.                                                                                                          |
| 5090 driver snags on Pop 24.04 (Blackwell install friction)                | Med        | Phase 0 live-USB proves it first; Windows intact throughout; upgrade path via System76 / graphics-drivers PPA.                                                         |
| Account lockout — a wiped Hello passkey was an account's only factor       | Med–High   | Phase 1 credential gate, verified per-account. **Now due before Phase 7 rather than Phase 3** — more runway, same requirement.                                         |
| **Offsite backup unopenable after total loss** (recovery-credential cycle) | **Med**    | [poindexter#889](https://github.com/Glad-Labs/poindexter/issues/889); store the four recovery secrets off-machine and **prove** recovery from an unrelated machine.    |
| Data loss during the handoff                                               | Low        | Quiesced final dump + test-restore; and with Windows retained, a bad restore is a reboot away from another attempt.                                                    |
| Postgres restore drift (roles, pgvector/pgcrypto)                          | Med        | `--globals-only` for roles; same image tag → same major; extension + row-count check — **already exercised successfully** 2026-07-18.                                  |
| RAM still tight (problem #2 unchanged)                                     | Med        | Reclaim the WSL tax now; 128 GB upgrade stays on the table.                                                                                                            |
| RTC skew between OSes corrupting cron fire-times                           | Low–Med    | Set `timedatectl set-local-rtc 0` + matching Windows UTC handling; don't leave it unmanaged.                                                                           |
| iCUE LINK tools contend for devices                                        | Low        | Validate coexistence; assign ownership per device.                                                                                                                     |

## Rollback

**This is the point of the redesign.** Through Phase 6, rollback is: reboot into Windows, restore the retained final dump, flip `OWNER.txt`. No reinstall, no restore-from-scratch, no race against a wiped drive. Windows remains a fully configured, bootable fallback for the entire evaluation.

Only after Phase 7 does rollback revert to "reinstall Windows from backup" — and by then the decision is evidence-backed rather than a bet.

## Open decisions / deferred

- **MP700's role after the wipe.** Either (a) reinstall Linux onto the fast MP700 and demote the MP600 to `/data`, or (b) keep Linux on the MP600 and use the MP700 as `/data`. **Recommend (a)** — Postgres and Docker benefit most from the PCIe5 drive, and by Phase 7 the whole build is scripted (`systemd` units, compose, restore path), so a second install is cheap. Decide at Phase 7 with real performance data.
- **Driver target version** (585 stock vs 610-class) — resolve empirically against `nvidia-container-toolkit` + Ollama CUDA needs.
- **GPU metrics:** keep the Python `nvidia-smi` scraper vs adopt `dcgm-exporter` — decide in Phase 5.
- **Incremental orchestration port:** only the minimal Linux set ships at cutover. Remaining PowerShell utilities (`deploy-worker`, `kuma-backup`, `prune-mcp-orphans`, `voice-brain-host`, …) port incrementally. (YAGNI.)
- **128 GB RAM** — separate hardware decision, not gated by this migration.

## Success criteria / definition of done

**For the dual-boot cutover (Phase 4–5):**

1. Both GPUs enumerated and usable **in a container** (`docker run --gpus all … nvidia-smi`).
2. A content-pipeline task runs **end-to-end** on the graph_def path with GPU (writer on 5090, vision on 3090).
3. `localhost:5433` + host CLI (`poindexter tasks list`) + the `postgres` MCP all reach the DB — **no wedge, no workaround** — under connection churn.
4. Grafana, alerts, brain heartbeat and self-heal green; scheduled agents fire on `systemd` timers.
5. Both OSes still boot; `OWNER.txt` reads `linux`; the Windows stack cannot auto-start.
6. Public site unaffected throughout.

**For committing to Linux (Phase 7):** the exit criteria above, met over ≥14 days, plus the credential gate ✅ and the [#889](https://github.com/Glad-Labs/poindexter/issues/889) recovery path proven.

## References

- `docs/superpowers/specs/2026-07-10-docker-ce-wsl2-migration-design.md` — the WSL half-measure (NO-GO); volume tiering + port inventory reused here.
- [Glad-Labs/glad-labs-stack#295](https://github.com/Glad-Labs/glad-labs-stack/issues/295) — bare-metal Linux umbrella (this spec).
- [poindexter#889](https://github.com/Glad-Labs/poindexter/issues/889) — offsite backup recovery-credential cycle.
- liquidctl HXi/RMi PSU guide — `--single-12v-ocp`, kernel-driver caveat.
- OpenLinkHub — iCUE LINK System Hub on Linux.
- Memory: `project_vram_oversub_docker_crashes` (the three crash classes), `user_profile` (hardware build), `project_gladlabs_infra` (local-first architecture).
