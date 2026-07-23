# Windows 11 → Pop!\_OS Dual-Boot Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to work this plan phase-by-phase with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking. This is an **ops/migration runbook**, not a code feature: "tests" are **verification gates** — run a command, confirm the expected output.

> ## 📍 RESUME HERE — migration status (last updated 2026-07-23)
>
> A session picking this up: read this block first, then jump to the phase named under "Next".
>
> - **✅ TASK 4.5 DONE — LINUX NOW OWNS THE DATABASE (2026-07-23, LOCAL Pop session).** Restored the FINAL dump (`/media/mattm/MP600/migration-backup-FINAL/poindexter-backup-20260723-013556`; NTFS mounted **read-only by label** — the `nvme?` numbers flipped again, MP600=`nvme1n1` this boot) with **zero drift** — all 7 baseline tables exact (posts 329 · pipeline_tasks 1960 · app_settings 1254 · embeddings 52773 · brain_knowledge 118 · atom_runs 6413 · audit_log 203707). Followed the dump's own `RESTORE-RECIPE.txt` (`<container>__<db>.dump` naming, streamed via stdin — the plan's flat `pg/<db>.dump` recipe was superseded), restored the secret-bearing globals (`poindexter.secret_key` GUC present), then brought up a **34-service core** — API `:8002/api/health` → 200, Grafana `database: ok`, DB port **40/40 under churn** (wedge-class gone by architecture; `pg_isready` isn't installed on the host, so verified via bash `/dev/tcp`), migrations **0 pending**, app_settings unchanged at 1254. **Blocker fixed:** `bootstrap.toml` `poindexter_deploy_root` was a leftover Windows path `C:/Users/…` → repointed to `/home/mattm/glad-labs-website` (it broke the brain/worker code bind-mounts — the only thing stopping the core `up`). **Deferred (NOT handoff-blocking):** `voice-agent-livekit` image build fails on docker-ce (its `curl … setup_20.x | bash-` Node install → `E: Unable to locate package nodejs`; voice not load-bearing), `image-gen-server`/`speaches` unbuilt, `wan-server`(built, VRAM-heavy)/`backup-*`(Phase-5 DR)/`github-runner`(PEM) not started; host Python lacks `asyncpg` so start-stack's grafana-webhook + offsite-backup secret pre-decrypt warns fail-soft. **Phase-4 tail = Ollama model staging:** `nomic-embed-text` pulled (fixed active embed 404s); standard set (`llama3.2:3b`/`phi4:14b`/`qwen3-vl:30b`/`qwen2.5:32b`) pulling; **custom `gemma-4-31B-it-qat` (local workhorse, ~25 `*_model` pins) + `glm-4.7-5090` (pipeline_architect) → rebuild from Hugging Face GGUFs (Matt's call 2026-07-23 — NOT a Windows boot; no `ollama create` recipe survives in repo/backup, so Matt re-pulls the GGUF and Claude writes the Modelfile + `ollama create` + verifies).** Primary writer is cloud `anthropic/claude-sonnet-5`, so drafting works meanwhile. **NEXT = finish model staging, then Phase 5 (systemd orchestration re-home) → Phase 6 (≥14-day eval).** Rollback still = reboot Windows + restore + flip `OWNER.txt` (reads `linux`).
> - **Phase 0 — Live-USB validation: ✅ COMPLETE = GO** (verified 2026-07-19, live demo session, **no drive written**). UEFI ✅ · both GPUs 5090+3090 on driver 595.84 ✅ · display 3440×1440 ✅ · drives MP600=`nvme1n1` / MP700=`nvme0n1` ✅ · network 0% loss ✅ · PSU single-rail ✅. The Blackwell-5090 NO-GO risk is closed.
> - **Where the operator is now:** **Phase 4 build-out on the installed Pop desktop (2026-07-22) — pre-handoff.** Phase 3 is DONE: MP600 shrunk via GParted, Pop installed with its own ESP, **both OSes boot** (Windows verified reaching desktop = rollback path intact; Task 3.4 gate effectively passed). Docker + GPU are up.
>   - ✅ **Done:** `docker-ce` + compose installed; `nvidia-container-toolkit` wired (`docker run --gpus all … nvidia-smi -L` shows **both** 5090 + 3090 in-container, Task 4.1); Windows-side RTC reg key `RealTimeIsUniversal=1` set + Linux half `timedatectl set-local-rtc 0` done (Task 3.5 complete); **Docker Desktop auto-start disabled on Windows** (defensive only — Windows STILL owns the DB, NOT a handoff).
>   - ✅ **Pre-handoff build-out DONE — Tasks 4.2 + 4.6 + 4.7-Step-1 (2026-07-22, LOCAL session).** Mounted the MP600 NTFS backup read-only **by stable by-id** (the `nvme?` number had **flipped a THIRD time** — MP600=`nvme1n1`, MP700=`nvme0n1` this boot; a plain `nvme0n1p2` mount hit the MP700's MSR → "NTFS signature missing"; by-id handles are in the device-flip note below). Restored `~/.poindexter` (bootstrap.toml + secrets + DR offsite env; **selective**, not the 42 GB of logs/media) and remapped the `~/.claude` 351-file memory store into the Linux project path; repo already cloned. **Ollama ×2 up + enabled** (`:11434` both-GPU / `:11435` 3090-pinned by UUID, units `User=mattm`, `OLLAMA_MODELS=/data/ollama/models`) — **no models staged yet** (recover the custom writer model from the still-alive Windows/WSL side during the 4.4 Windows boot). **Tailscale up** as `nightrider-1` (`100.111.15.72`; `nightrider` still held by the offline Windows node). **Deferred:** ufw docker-only gate (post-handoff — needs the stack up + a LAN-vs-tailnet access decision to avoid an SSH/tailnet lockout) and volume restore (4.2 S3). **Done since:** `timedatectl set-local-rtc 0` (Task 3.5 Linux half); dev-env readied — Node 18→22 (NodeSource, system-wide) + `npm install` + husky pre-commit lint verified working. **Dropped:** `claude-telegram` autostart (no longer wanted — was 4.7 S2–5).
>   - ✅ **DB handoff — WINDOWS HALF DONE 2026-07-23 (Task 4.4 Steps 1–7 + gate Step 9).** Quiesced (14 writer/resurrector scheduled tasks disabled + `gladlabs-docker.cmd` Startup stack-starter renamed `.disabled-migration`; all containers stopped), **proved quiet** (`audit_log` 203707 stable across a 35s double-sample, 0 active conns), took the **FINAL dump** to `D:\migration-backup-FINAL\poindexter-backup-20260723-013556` and **test-restored `poindexter_brain` on Windows with 0 drift** (baseline in that dir's `BASELINE-and-verification.txt`: posts 329 · pipeline_tasks 1960 · app_settings 1254 · embeddings 52773 · brain_knowledge 118 · atom_runs 6413 · audit_log 203707), then wrote `OWNER.txt=linux` to `D:\migration-backup`. **✅ DONE 2026-07-23 — restored on Pop with zero drift, 34-service core stack live, Linux owns the DB (see the "TASK 4.5 DONE" line at the top of this block).** The essential handoff inputs (FINAL dump + `BASELINE-and-verification.txt` + `OWNER.txt`) all live on the shared NTFS `D:` partition, so the Pop session has them even without this repo. ⚠️ `backup-precious.sh` had a Windows `docker cp` host-path bug (MSYS-off mangled `/d/…`→`C:\d\…`) — fixed locally by streaming `pg_dump -Fc > file`; upstream fix is to scope MSYS per-arg.
>   - **Display note:** slight screen flicker on the ultrawide was **FreeSync/VRR** — turned off in the monitor OSD, resolved. Log as a resolved daily-driver item for the Task 6.2 eval, not a blocker.
>   - **🖥️ This state was reached over a cloud Claude Code session (copy-paste). Handoff to a LOCAL session on the Pop box is in progress** — it can run these steps directly. Start it inside `~/glad-labs-website` once cloned.
> - **🚨 DEVICE-NUMBER FLIP (2026-07-22) — READ BEFORE ANY PARTITION OP.** The `nvme?`/Disk numbering is **not stable across boots**. This boot: **MP600 = `nvme0n1` (install target, shrink `nvme0n1p2`)**, **MP700 = `nvme1n1` (Windows C: on `nvme1n1p3` — DO NOT TOUCH)** — the **opposite** of the 2026-07-19 Phase 0 record. Nearly caused a live-Windows resize. **Identify every disk by MODEL / Windows-layout, never by number** (Task 0.2 Step 5 has the full table). **Stable handles that never flip across boots — prefer these for every mount/dump/restore target:** MP600 (Pop target) = `/dev/disk/by-id/nvme-Corsair_MP600_CORE_XT_A631B443009QQD`; MP700 (Windows C:/DB — off-limits till the handoff) = `/dev/disk/by-id/nvme-Corsair_MP700_ELITE_with_Heatsink_AA0CB507000XOS`. Confirmed flipped a **3rd** time on the 2026-07-22 local-session boot (MP600=`nvme1n1`, MP700=`nvme0n1`) — the numbers are pure coin-flip, the by-id handles are not.
> - **✅ ALL FIVE PRE-PHASE-3 GATES ARE CLOSED (2026-07-19).** G1 ✅ · G2 ✅ · G3 ✅ · G4 ✅ · **G5 ✅**. **Next action is Phase 2 (BIOS fan floor), then Phase 3 — the MP600 shrink + Pop!\_OS install.** That is the first destructive step; everything before it was reversible.
> - **G5 evidence (2026-07-19):** `powercfg /a` reports `Hibernate — Hibernation has not been enabled` and `Fast Startup — Hibernation is not available`; `C:\hiberfil.sys` is **absent**; System event log shows a clean `6006 → 6005` pair with **no 6008 and no 41** (no unexpected/dirty shutdown). Note `HKLM\...\Power\HiberbootEnabled` still reads `1` — that is **moot and expected**: Fast Startup cannot function without hibernation, and `powercfg` reports it unavailable. Do not "fix" that registry value. One check needs elevation and was left for the operator: `fsutil dirty query D:` should say **not Dirty** before Linux mounts it read-write.
> - **📦 Backup coverage — verified 2026-07-19, every artifact has ≥2 copies OFF the MP600** (the condition that actually gates repartitioning):
>
>   | artifact                  | D: MP600 _(being shrunk)_ | C: MP700    | B2 offsite | F: USB        |
>   | ------------------------- | ------------------------- | ----------- | ---------- | ------------- |
>   | 5 DB dumps                | ✅                        | ✅ two sets | ✅         | ⚠️ brain only |
>   | config + `bootstrap.toml` | ✅                        | ✅          | ✅         | ✅ daily      |
>   | `~/.claude` memory        | ✅                        | ✅          | ✅         | ✅            |
>   | Docker volumes 10.8 GB    | ✅                        | ✅          | ✅         | ❌            |
>
>   The **volumes row was single-copy on the MP600 until 2026-07-19** — copied to `C:\migration-backup-copy\volumes` and all 6 archives SHA256-verified identical. `gladlabs-pgadmin-data` (17 KB) and `gladlabs-postiz-uploads` (27 KB) are **valid, just genuinely tiny** — do not read small as failed.
>
> - **⚠️ `globals.sql` is secret-bearing.** `pg_dumpall --globals-only` emits `ALTER ROLE … SET "poindexter.secret_key" TO '<master key>'` plus the SCRAM password hash in plaintext. Handle it exactly like `bootstrap.toml` — never commit, never print, never paste. When verifying it, stream to a hash rather than to a file or the terminal.
> - **✅ #889 recovery-secret proof (Task 1.4 Step 5) — DONE 2026-07-20.** Proven on a separate PC with no access to this box: the three carried secrets opened the B2 repo and restored `bootstrap.toml` (master key inside). The last non-hardware Phase 7 gate is cleared; the offsite repo is provably self-sufficient.
> - **⚠️ Handoff / provenance:** this progress lives in _this file's_ checkboxes + dated notes, and **everything through 2026-07-19 is merged to `main`** (PRs #2712-#2723). No branch checkout needed. When you complete a step, tick its box here so the next session inherits accurate state.
> - **☁️ IF YOU ARE A CLOUD SESSION, read this.** You can see this repo and the GitHub issues. You **cannot** see the operator's machine, and you cannot see `~/.claude/projects/*/memory/` — the local memory store where a lot of this migration's context was accumulated. This file is therefore the handoff surface: if something isn't written here, a cloud session does not know it. Two consequences: (1) every hardware/drive/backup claim above is a **recorded observation**, not something you can re-verify — do not assert it is still true, ask the operator to confirm before anything destructive; (2) the F: DR backup system below exists only on the operator's box and in this note.
> - **🔌 There is a THIRD backup system the rest of this plan never mentions — and the migration silently kills it.** A 117 GB USB stick (`F:`) holds a restic repo at `F:/poindexter-backup`, fed by **two Windows Task Scheduler jobs**: `GladLabs-DR-Backup` (daily 03:00, tag `dr-daily`, retention 7d/4w/6m) and `GladLabs-DR-Backup-Hourly` (hourly, tag `pg-hourly`, keep-last 24). Both shell out through Git Bash to `~/.poindexter/scripts/dr-backup/{run-backup,run-hourly-pg}.sh`. **Those operator-local files are what actually runs**, but as of PR #2725 an equivalent, de-identified copy is committed at **`scripts/dr-backup/`** — before that they existed nowhere but the machine they protect and the snapshots they produce, which made recovering the tooling depend on already being able to open the repo. Nothing repoints the scheduled tasks at the repo copy; that is Task 5.4 Step 2. `restic.exe` lives at `~/bin/restic.exe` (not on `PATH`, which is why `which restic` finds nothing). The repo passphrase is `poindexter_backup_passphrase` in `bootstrap.toml` — **a different secret from the B2 repo's `RESTIC_PASSWORD`**, so F: is an _independent_ recovery path, and it contains `bootstrap.toml`, so opening it returns everything. **Task 5.1 ports the ops sessions and does NOT cover these two jobs — see Task 5.4.**

**Goal:** Move the Poindexter operator stack from Windows 11 + Docker-Desktop/WSL2 onto bare-metal Pop!\_OS 24.04 — both GPUs, host-native Ollama, orchestration and hardware config — **without giving up the ability to go back**, and with the public site untouched throughout.

**Architecture:** Pop!\_OS installs to the **MP600 alongside Windows** on the MP700. Both OSes stay bootable; separate physical drives mean separate ESPs, so neither can break the other's boot path. Linux takes ownership of the database in a single explicit handoff, runs the business for a ≥14-day evaluation, and only then is Windows wiped. Native `docker-ce` republishes the identical 18 host ports, so `bootstrap.toml`/DSN are zero-touch.

**Tech Stack:** Pop!\_OS 24.04 (COSMIC), `docker-ce` + compose v2, `nvidia-container-toolkit`, native Ollama, `systemd`, `liquidctl`, `node_exporter`, Tailscale, Poindexter (Python/FastAPI/Postgres).

**Spec:** [`docs/superpowers/specs/2026-07-17-windows-to-pop-os-migration-design.md`](../specs/2026-07-17-windows-to-pop-os-migration-design.md). Read it for the _why_ — especially "Split-brain: the governing rule" and the exit criteria. This plan is the _how_.

## Global Constraints

**The three that will actually hurt if ignored:**

- **⛔ ONE OS OWNS THE DATABASE AT A TIME.** Never run the stack on both. Two Postgres instances diverge with no merge path and no reconciliation tool — a post published on one side simply does not exist on the other. Handoff is a deliberate ceremony (Task 4.4), recorded in `D:\migration-backup\OWNER.txt`, which both OSes can read. **Cut over; do not alternate.**
- **⛔ THE PRECIOUS TIER LIVES ON TWO DEVICES THAT ARE NOT THE MP600** before the MP600 is repartitioned. It currently holds the plain-dump backup, and Phase 3 resizes it.
- **⛔ DISABLE WINDOWS FAST STARTUP** before Linux ever mounts the NTFS partition. Fast Startup leaves the filesystem hibernated and dirty; a read-write mount from Linux can corrupt it — including the backup living on it.

**Standing technical constraints:**

- **Public site is decoupled** — Vercel + R2 serve gladlabs.io throughout. Never a factor in downtime, in any phase.
- **Preserve the 18 published ports exactly** (`8002 3000 5433 3010 8080 9091 9093 3100 3200 4200 4040 3002 5003 18443 8001 9836 9840 7880-7882`). Same ports → host tooling + `bootstrap.toml`'s `localhost:5433` DSN need no change.
- **Native-Linux Docker networking rules (non-obvious, load-bearing):**
  - Containers reach host services via `host.docker.internal`, which on `docker-ce` requires `extra_hosts: ["host.docker.internal:host-gateway"]` (it is NOT automatic like Docker Desktop).
  - Host services containers must reach (Ollama, node_exporter) **bind `0.0.0.0`, not `127.0.0.1`** — a loopback bind is unreachable from the docker bridge. Gate external access with `ufw` instead.
- **GPU indexing:** `nvidia-smi -i 0` = RTX 5090, `-i 1` = RTX 3090. Pin by **UUID** with `CUDA_DEVICE_ORDER=PCI_BUS_ID` (a bare numeric index is unreliable).
- **WSL2 volumes are unreachable from Linux** — they live inside a VHDX (ext4 in a VM disk). Data moves via **dumps**, never file copies. The NTFS `D:` partition _is_ readable from Linux, so it is the handoff channel; no USB shuttle needed.
- **Secure Boot is OFF for the whole dual-boot period.** Pop!\_OS ships no Secure-Boot-signed shim, and its current build's bootloader is on Microsoft's revocation list — so Secure Boot on means a "Security Violation" screen instead of a boot menu. Windows 11 boots fine without it (it is an _installation_ requirement, not a runtime one), but treat this as a real, semi-permanent change to the machine's security posture rather than a toggle. Always **check BitLocker before changing it** (Task 0.1 Step 5) — the PCR shift triggers recovery-key prompts.
- **Repo lives at** `~/glad-labs-website` (clone fresh); deploy checkout + worktree roots re-created under `~/.poindexter/`.
- **Shipped `systemd` units are generic templates** (`User=poindexter`, `/home/poindexter/…`, for public-mirror hygiene). Either set `User=mattm` + the `/home/mattm/…` paths in each unit, or run the stack under a dedicated `poindexter` login. The `scripts/linux/*.sh` are `$HOME`-relative and need no edit.
- **Nothing here is irreversible until Phase 7.** Through Phase 6, rollback is: reboot to Windows, restore the retained final dump, flip `OWNER.txt`.
- **`docs/superpowers/` is stripped from the public mirror** — this plan is operator-internal.

## Phase map

| Phase | What                                   | Reversible?                              |
| ----- | -------------------------------------- | ---------------------------------------- |
| 0     | Live-USB hardware validation           | Nothing touched                          |
| 1     | Backup + credential re-homing          | Nothing touched — **backup ✅ verified** |
| 2     | BIOS fan floor + Windows pre-flight    | Trivially undone                         |
| 3     | Shrink MP600, install Pop alongside    | Windows untouched and bootable           |
| 4     | Build the stack on Linux + **handoff** | Reboot + restore                         |
| 5     | Re-home orchestration to systemd       | Reboot + restore                         |
| 6     | **Evaluation** (≥14 days, measured)    | Reboot + restore                         |
| 7     | ⛔ Wipe Windows                        | **The only one-way door**                |

---

## Phase 0 — Live-USB hardware validation (GO/NO-GO, zero risk)

No drive is touched. Proves the hardware runs Pop!\_OS before anything destructive.

### Task 0.1: Build the Pop!\_OS live USB

**Files:** none (external media).

- [ ] **Step 1: Download the Pop!\_OS 24.04 NVIDIA ISO** from https://system76.com/pop/download (the NVIDIA variant — ships the proprietary driver).
- [ ] **Step 2: Pick the target stick — carefully.** Verified 2026-07-18: **`F:` is a live restic backup repository** (`~/poindexter-backup`, written hourly by the backup containers). **Do not flash `F:`.** Use **`E:`** (SanDisk 29 GB, FAT32, unused) or another spare.
- [ ] **Step 3: Flash with Rufus** (GPLv3, ~1.4 MB portable, no install — https://rufus.ie, source `pbatard/rufus`). Select the ISO and the `E:` device, leave the defaults.

> **⚠️ When Rufus asks "ISO Image mode" vs "DD Image mode", choose DD Image mode.**
> Pop!\_OS ships a hybrid ISO. ISO mode produces a stick that _looks_ written and
> then will not boot — the single most common way this step fails.

> **⚠️ Rufus will warn "Revoked UEFI bootloader detected." This is expected — click OK.**
> Rufus checks the ISO's bootloader against Microsoft's DBX revocation list, and the
> GRUB2/shim revocations from the BootHole CVE family invalidated most distro
> bootloaders. It means the shim is **older than the revocation list**, not that the
> ISO is malicious. Rufus suggests "find a more up-to-date version" — on Pop!\_OS that
> is a **dead end**: build 26 is current and still ships a pre-revocation shim.
> Disabling Secure Boot (Step 6) is the path, not a workaround.

- [ ] **Step 4: Verify the ISO checksum** — this, not the Rufus warning, is what distinguishes "old bootloader" from "tampered download":

```powershell
Get-FileHash -Algorithm SHA256 "$env:USERPROFILE\Downloads\pop-os_24.04_amd64_nvidia_26.iso"
```

Compare against System76's published value. The API serves it directly, no scraping:
`https://api.pop-os.org/builds/24.04/nvidia` → `sha_sum`.

**Verified 2026-07-18** — build 26 matched byte-for-byte:
`6ba3e68cc2f96d133b2dab8ec04d852d8089888dfcb6d9e68fb6a70aab0d3776`

- [ ] **Step 5: Check BitLocker BEFORE touching firmware settings** (elevated PowerShell):

```powershell
Get-BitLockerVolume | Select-Object MountPoint, VolumeStatus, ProtectionStatus
```

**Why this comes first:** changing Secure Boot alters the TPM's PCR measurements, and BitLocker reads that as tampering — it will demand the 48-digit recovery key on the next Windows boot. Locking yourself out of Windows _precisely as it becomes the rollback path_ is the worst possible timing.

- If `ProtectionStatus` is **On**: `Suspend-BitLocker -MountPoint "C:" -RebootCount 0` first, retrieve the recovery key (account.microsoft.com/devices/recoverykey), and `Resume-BitLocker` once Windows is confirmed booting after the BIOS change.
- If **Off**: nothing to do. _(Verified Off on this machine 2026-07-19.)_

- [ ] **Step 6: Disable Secure Boot** (BIOS → Del). System76 does not ship a Secure-Boot-signed shim; with it enabled you get a "Security Violation" screen instead of a boot menu.

> **This is a standing change, not a temporary toggle.** Secure Boot stays **off for the
> entire dual-boot period**, because you will be booting Pop daily. Windows 11 boots
> fine without it — Secure Boot is an _installation_ requirement, not a runtime one —
> but this is a real change to the machine's security posture. Restoring it after
> committing to Linux is a MOK-enrollment exercise, not a checkbox.

- [ ] **Step 7: Keep this stick.** On a dual-boot machine it is your **rescue media**, not disposable installer media — it is what you boot if an EFI entry lands wrong or a bootloader needs repair. (A Ventoy stick, which holds multiple ISOs, is a nice follow-up afterwards — but don't add that abstraction layer during the migration itself.)

### Task 0.2: Boot the live session and validate core hardware

- [ ] **Step 1: Boot the USB.** F8 (ASUS boot menu) → **pick the entry prefixed `UEFI:`**. There are usually two entries for the same stick; the non-UEFI one boots legacy/CSM. At the Pop menu choose **Try Demo Mode** — do **not** click Install.
- [x] **Step 2: Confirm you actually booted UEFI — do this first.** Everything downstream assumes it, and getting it wrong stays silent until it breaks dual-boot much later:

```bash
ls /sys/firmware/efi >/dev/null 2>&1 && echo "UEFI ✓" || echo "LEGACY — reboot via the UEFI: entry"
```

**Expected:** `UEFI ✓`. If it reports LEGACY, reboot and pick the `UEFI:` entry — a legacy install cannot cleanly dual-boot against a UEFI Windows, and Phase 3's separate-ESP design depends on it.
**Verified 2026-07-19 — live session printed `UEFI`. ✅**

- [x] **Step 3: Verify both GPUs enumerate — this is the real gate:**
      `nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv`
      **Expected:** two rows — `0, NVIDIA GeForce RTX 5090, 32607 MiB, <ver>` and `1, NVIDIA GeForce RTX 3090, 24576 MiB, <ver>`.
      **If only the 3090 appears, that is the Blackwell driver gap** — note the driver version and stop. NO-GO. If `nvidia-smi` is missing or errors entirely, same verdict.
      _If the live session boots to a **black screen**, that is a signal rather than a dead end: retry with Pop's **safe graphics** option. Reaching a desktop only in safe graphics means the shipped driver cannot drive the 5090 — treat it as the same NO-GO._
      **Verified 2026-07-19 — BOTH GPUs enumerate on driver `595.84`: `0, RTX 5090, 32607 MiB` and `1, RTX 3090, 24576 MiB`. The Blackwell driver gap is CLOSED — the live NVIDIA ISO drives the 5090 out of the box, and 595.84 is newer than the ~585 this plan assumed (see Task 3.6). ✅**
- [x] **Step 4: Verify the display** runs the Acer ultrawide at native `3440x1440`.
      **Read it from COSMIC → Settings → Displays.** COSMIC on 24.04 is **Wayland**, so `xrandr` either fails or reports XWayland's view rather than the truth — do not trust it here.
      **Verified 2026-07-19 — native 3440×1440 in COSMIC Displays. ✅**
- [x] **Step 5: Verify both NVMe drives are seen — and record which is which:**
      `lsblk -d -o NAME,MODEL,SIZE` → expect two ~1.8T devices, one MP700 and one MP600.
      **Write down the `nvme?n1` → model mapping.** Phase 3 partitions the **MP600** specifically; confusing the two there is the expensive mistake, and Linux device order need not match Windows' Disk 0/Disk 1 numbering.
      **⭐ RECORDED 2026-07-19 (live session `lsblk`):**
      | Linux device | Model | Size | Role in Phase 3 |
      | ------------ | ----- | ---- | --------------- |
      | `nvme1n1` | **Corsair MP600 CORE XT** | 1.8T | ⛔ **INSTALL TARGET** — shrink + install Pop here |
      | `nvme0n1` | **Corsair MP700 ELITE with Heatsink** | 1.8T | Holds Windows — **NEVER written to** |

      > **🚨 THE `nvme?` NUMBERING IS NOT STABLE ACROSS BOOTS — it FLIPPED on 2026-07-22.** On the GParted boot the SAME hardware enumerated the **opposite** way:
          >
          > | Linux device | Model | 2026-07-22 layout | Role |
          > | ------------ | ----- | ----------------- | ---- |
          > | **`nvme0n1`** | **Corsair MP600 CORE XT** | p1 16M MSR · **p2 1.8T ntfs "MP600"** | ⛔ **INSTALL TARGET** — shrink `nvme0n1p2` |
          > | **`nvme1n1`** | **Corsair MP700 ELITE with Heatsink** | p1 100M vfat ESP · p2 16M MSR · **p3 1.8T ntfs "MP700" = Windows C:** · p4 797M recovery | **WINDOWS DRIVE — NEVER TOUCH** |
          >
          > This nearly caused a disaster: keying off the 2026-07-19 mapping, `nvme1n1` was about to be resized in GParted — but on this boot `nvme1n1` is the **MP700 (Windows C:)**. The `MP700` partition **label** on `nvme1n1p3` is what caught it. **NEW STANDING RULE: at every destructive step, identify each disk by its MODEL string and/or the Windows layout (ESP+MSR+C:+Recovery = the drive to AVOID), NEVER by the `nvme0`/`nvme1` number.** Volume labels (`MP600`/`MP700`) happen to track the models here and are a useful second signal; the bare device number is not.

          **⚠️ Note (from the original 2026-07-19 boot):** the install target was `nvme1n1` then — but per the flip above, do not rely on that. Confirm the model reads **`MP600 CORE XT`** in `lsblk`/GParted Device Information before touching any partition. (USB installer media appeared as `sda`/`sdb`; `loop0` is the live squashfs; `zram0` is live-session swap — none are install targets.)

- [x] **Step 6: Verify network** (Ethernet or Wi-Fi): `ping -c3 1.1.1.1` → expect 0% loss. **Verified 2026-07-19 — 0% loss. ✅**

### Task 0.3: Confirm PSU visibility on Linux (informational — NOT a gate)

> **Resolved 2026-07-18:** the HX1500i stores the single/multi-rail toggle in its **own onboard memory**. The 2026-07-13 single-rail fix survives the wipe, iCUE's absence, and even a different machine — so this task no longer gates anything. It only confirms Linux-side telemetry.

- [x] **Step 1: Install liquidctl in the live session:** `sudo apt update && sudo apt install -y liquidctl` (or `pipx install liquidctl` if the apt version is old). **Done — the apt build detects the HX1500i.**
- [x] **Step 2: Detect the PSU:** `liquidctl list`
      **Expected:** the HX1500i appears (e.g. `Corsair HXi ... (experimental)`). If it does NOT appear → note it; the 2025 HX1500i USB ID may need a newer liquidctl (`pipx install --pip-args=-U liquidctl`). Re-test before deciding.
      **Verified 2026-07-19 — `Corsair HX1500i (experimental)` visible via `sudo liquidctl status`. ✅**
- [ ] **Step 3 (optional):** if you want the Linux-side re-assert available later, test `sudo liquidctl initialize --single-12v-ocp` — **always with the flag**, since a bare `initialize` resets the PSU to multi-rail.
      (If it errors that a kernel driver owns the device, retry with `--direct-access`, or `sudo modprobe -r corsair_psu` first.)
      **Expected:** initializes without error. **(Not needed now — Step 4 already reads single-rail; this is the Task 3.7 boot one-shot's job.)**
- [x] **Step 4: Read it back:** `sudo liquidctl status` → note the OCP/rail fields. **Not a blocker:** the single-rail setting lives in the PSU's onboard memory and survives the wipe regardless, so a liquidctl miss here costs only optional Linux-side telemetry.
      **Verified 2026-07-19 — `+12V OCP mode: Single rail` ✅ (the 2026-07-13 fix survived into Linux, exactly as the onboard-memory reasoning predicted); `Fan control mode: Hardware`; rails/temps healthy (11.94V, 54.5°C VRM). NOTE: the `Estimated input power` (3036 W) and `Estimated efficiency` (6%) fields are a known bogus-telemetry quirk of liquidctl on the HXi series — ignore them; the real signals are the rail voltages/currents/OCP mode.**

### Task 0.4: Validate iCUE LINK visibility (non-blocking)

- [ ] **Step 1:** Note that fans/pump retain their onboard curve regardless (confirmed) — this is a _nice-to-have_, not a gate.
- [ ] **Step 2 (optional):** Try OpenLinkHub per https://github.com/jurkovic-nikola/OpenLinkHub to confirm the iCUE LINK System Hub is visible for later RGB/fan control. Failure here does **not** block the migration.

**### PHASE 0 GATE:** Both GPUs ✅, display ✅, both drives ✅, network ✅. All green → **GO**. A GPU failure → **NO-GO**, Windows still fully intact. (PSU/liquidctl is informational only — the OCP setting persists in the PSU itself.)

> **✅ PHASE 0 = GO — verified 2026-07-19 (live demo session).** UEFI ✅ · both GPUs
> on driver 595.84 (5090 + 3090) ✅ · display 3440×1440 ✅ · both NVMe drives seen,
> MP600=`nvme1n1`/MP700=`nvme0n1` ✅ · network 0% loss ✅. PSU bonus: HX1500i visible,
> `+12V OCP = Single rail` confirmed on Linux. The Blackwell-5090 NO-GO risk is closed.
> **Nothing was written to any drive.** Next actions are on the Windows side (Phase 1
> backup completion + Phase 2 fan floor), then the ⛔ pre-Phase-3 gate before any
> destructive write.

---

## Phase 1 — Backup + credential re-homing (non-destructive)

Runs on the **current Windows box** against the live Docker-Desktop stack (Git Bash). Nothing is wiped.

### Task 1.1: Author the backup script

**Files:**

- Create: `scripts/linux/backup-precious.sh`

**✅ DONE — the script is committed at [`scripts/linux/backup-precious.sh`](../../../scripts/linux/backup-precious.sh).**
Read it there; it is deliberately **not** duplicated here, because an inline
copy drifts out of sync with the file that actually runs (the first version of
this plan did exactly that and shipped four wrong volume names).

- [ ] **Step 1: Read the script** before running it, so its failure modes are not a surprise mid-run.

**What a live run against the real stack corrected (2026-07-18)** — the original
draft was wrong in four ways, each of which would have produced a backup that
_looked_ successful:

| Assumption in the first draft            | Reality                                                                                                                                                                                      |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 6 hardcoded volume names                 | **4 of 6 were wrong.** Compose derives volume names from the project directory, so hardcoding them backs up nothing on a differently-named checkout. Now discovered from running containers. |
| One Postgres instance holds every DB     | **Three instances**, each with its own superuser — bundled services ship their own Postgres. Now discovered, not assumed.                                                                    |
| Tar the database volumes                 | A tar of a **live** data directory is a torn snapshot that may not replay. DB volumes are now excluded and captured as `pg_dump`s.                                                           |
| `cp -r ~/.poindexter` is a config backup | It is **32.7 GB**, of which 22.65 GB is superseded local DB backups. Now copies top-level config files only, and hard-fails if `bootstrap.toml` is absent.                                   |

The rewritten script therefore **discovers** containers, databases and volumes at
runtime rather than hardcoding them, and verifies every dump with
`pg_restore --list` before reporting success.

### Task 1.2: Run the backup to the MP600 and offsite

**Partially executed 2026-07-18** — staged locally to `D:\migration-backup`
(47.33 GB). This run predates the script rewrite, so it was driven step-by-step;
the corrected script now reproduces it in one command. **Offsite is still
outstanding (Step 3).**

- [x] **Step 1: Run the backup.** `bash scripts/linux/backup-precious.sh /d/migration-backup`

  Landed (~428 MB of dumps + 10.8 GB of volumes + 36 GB of config/media):

  | Artifact                      | Size      | Note                                                    |
  | ----------------------------- | --------- | ------------------------------------------------------- |
  | `pg/poindexter_brain.dump`    | 296.21 MB | the precious one                                        |
  | `pg/prefect.dump`             | 97.81 MB  |                                                         |
  | `pg/glitchtip.dump`           | 33.93 MB  | from its **own** Postgres container                     |
  | `pg/langfuse.dump`            | 0.42 MB   |                                                         |
  | `pg/postiz.dump`              | 0.17 MB   | from its **own** Postgres container                     |
  | `volumes/langfuse-clickhouse` | 10,703 MB | live-read; traces are re-derivable, best-effort is fine |
  | `volumes/` (5 others)         | ~117 MB   | grafana, minio, uptime-kuma, postiz-uploads, pgadmin    |
  | `config/dot-poindexter`       | ~0.1 MB   | `bootstrap.toml` **hash-verified** against source       |
  | `config/dot-claude`           | ~1.9 GB   | 3 × 351 memory `.md` files, all present                 |

- [x] **Step 2: Verify the dumps are non-empty** — all > 0 bytes; `poindexter_brain.dump` at 296 MB.
- [ ] **Step 3: Push to offsite.** Copy to Backblaze/R2 and **confirm the remote listing shows the same files and sizes** — do not assume the upload succeeded.

> **Note on `.claude`:** the copy reports `robocopy rc=9`. The only genuine
> failure is the dangling `debug/latest` symlink (it points at a rotated log).
> Everything that matters copied; `rc=9` here is expected, not a warning to chase.

### Task 1.3: Test-restore the precious DBs (proves the backup before the wipe)

**✅ EXECUTED AND PASSED 2026-07-18.** `pg_restore rc=0` in 22 s; `vector 0.8.2`,
`pgcrypto 1.3`, `pg_trgm`, `pg_stat_statements` all present; 768-dim KNN query
returned neighbours; 6/7 tables matched exactly. Re-run this before the wipe
against the FINAL dump from Task 3.0 — the run below only proves the rehearsal
copy.

- [ ] **Step 1: Capture the live baseline FIRST**, so there is something to compare against. Note the timestamp.

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -F'|' -c \
 "select 'posts',count(*) from posts union all select 'pipeline_tasks',count(*) from pipeline_tasks \
  union all select 'app_settings',count(*) from app_settings union all select 'embeddings',count(*) from embeddings \
  union all select 'brain_knowledge',count(*) from brain_knowledge union all select 'atom_runs',count(*) from atom_runs;"
```

- [ ] **Step 2: Spin a throwaway Postgres** on the same image pin. Use `trust` auth so no password is handled; restore `--no-owner` because the dump is owned by `poindexter`, not `postgres`.

```bash
docker run -d --name pg-restore-test -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_USER=poindexter -e POSTGRES_DB=postgres --memory=4g pgvector/pgvector:pg16
until docker exec pg-restore-test pg_isready -U poindexter -q; do sleep 1; done
```

- [ ] **Step 3: Restore:**

```bash
docker cp /d/migration-backup/pg/poindexter_brain.dump pg-restore-test:/tmp/brain.dump
docker exec pg-restore-test createdb -U poindexter poindexter_brain
docker exec pg-restore-test pg_restore -U poindexter -d poindexter_brain \
  --no-owner --no-privileges -j 4 /tmp/brain.dump
```

**Expected:** `rc=0`. Errors here mean the backup is broken — fix it **now**.

- [ ] **Step 4: Verify extensions are functional, not merely installed.** "Extension exists" does not prove the data survived:

```bash
docker exec pg-restore-test psql -U poindexter -d poindexter_brain -t -A -c \
  "select vector_dims(embedding::vector) from embeddings where embedding is not null limit 1;"
docker exec pg-restore-test psql -U poindexter -d poindexter_brain -t -A -c \
  "select count(*) from (select id from embeddings where embedding is not null \
    order by embedding <-> (select embedding from embeddings where embedding is not null limit 1) limit 5) s;"
```

**Expected:** a dimension count (768) and `5`. A KNN query that returns rows proves the vectors restored intact.

- [ ] **Step 5: Compare row counts to the Step 1 baseline.**

> **⚠️ Expect a small shortfall on append-only tables, and do not panic.** `pg_dump`
> snapshots at the moment it **starts**, not when the file finishes writing. On the
> 2026-07-18 run `audit_log` restored 197,719 against a live 197,805 — the missing
> 86 were all written _after_ the snapshot. Confirm drift rather than assuming it:
> every restored count must equal the live count **as of the dump's start**, i.e.
> `select count(*) from audit_log where "timestamp" <= '<dump-start>'`. A shortfall
> on a table that should be **static** (`posts`, `app_settings`) is a real failure.
> This drift is harmless now because Windows still holds the live DB — it is exactly
> what **Task 3.0** exists to eliminate at cutover.

- [ ] **Step 6: Tear down:** `docker rm -f pg-restore-test`.

### Task 1.4: Credential re-homing (the unrecoverable-after gate)

**Files:** none (manual account work — Matt only).

- [ ] **Step 1: Inventory device passkeys** — Windows **Settings → Accounts → Passkeys**; screenshot the full list.
- [ ] **Step 2: Sweep top-tier accounts** in blast-radius order — Backblaze, Mercury, domain registrar, Cloudflare, Google, GitHub, Vercel, Anthropic, Resend, HuggingFace — for a passkey/security key bound to this PC.
- [ ] **Step 3: Re-home each** onto the Pixel (passkey/authenticator) **while Hello still works**; save printed recovery codes for the top-tier set; harden the Google account's own recovery.
- [ ] **Step 4: GATE** — confirm no account's only surviving factor is a wiped Hello passkey. This must be ✅ before **Phase 7** (the wipe), which is what destroys the TPM-bound passkeys. Dual-boot buys runway here, not an exemption: the requirement is unchanged, the deadline just moved.

#### Step 5: ⛔ Break the offsite-backup circular dependency (discovered 2026-07-18)

**The automated offsite backup currently cannot be opened after a total loss.**
This is **not** a migration bug — it is true right now — but **Phase 7** makes it
acute, because the wipe destroys the machine holding the only copy of the key.

Verified state: `offsite_backup_enabled=true`, streaming a fresh `pg_dump` of
`poindexter_brain` to `s3:…backblazeb2.com/poindexter-backup/poindexter` every
24 h, last snapshot confirmed OK. Healthy — and unopenable, because:

1. The only offsite artifact is the **B2 restic repo**.
2. Opening it requires the **restic password** + **B2 access keys**.
3. Those live **encrypted in `app_settings`**, inside `poindexter_brain`.
4. `poindexter_brain` is inside the repo you cannot open. ⟲
5. The key that decrypts them, **`poindexter_secret_key`, exists only in
   `~/.poindexter/bootstrap.toml`** — which the offsite runner never backs up
   (`scripts/backup-offsite/run.sh` streams exactly one database, nothing else).

Lose the MP700 **and** the MP600 and every copy of the key is gone, leaving a
verified daily backup that no one can decrypt.

> **✅ UPDATE 2026-07-19 — the repo is now self-sufficient, so the kit shrank to
> THREE secrets.** The G4 `precious` push put **`bootstrap.toml` inside the B2
> repo** (round-trip verified). Before that, the repo held only
> `poindexter_brain.dump`, so even a successful open yielded a database whose
> `app_settings` secrets were encrypted with a key that existed nowhere else —
> partial, undecryptable recovery. Now the repo carries the master key itself,
> so opening it returns everything. **`poindexter_secret_key` no longer needs to
> be stored separately** — it is recoverable _from_ `bootstrap.toml` once the
> repo opens. The cycle is now **breakable but not yet broken**: the three
> opening secrets still live only on this machine.

**The recovery kit — three secrets plus one non-secret string:**

| Value                   | Secret? | Where it lives today                              |
| ----------------------- | ------- | ------------------------------------------------- |
| repo URL                | no      | `app_settings.offsite_backup_repository`          |
| `RESTIC_PASSWORD`       | yes     | `.poindexter-backup-offsite.env` (379 B, 3 lines) |
| `AWS_ACCESS_KEY_ID`     | yes     | same file                                         |
| `AWS_SECRET_ACCESS_KEY` | yes     | same file                                         |

- [x] **Step 5a: Put those three secrets outside both the machine and the bucket** — the Pixel's password manager (which Step 3 already re-homes) and/or printed and stored offline. **Matt only — do not automate, and do not let an agent read them.** **DONE 2026-07-20 — the three secrets were carried to a separate PC (below) with no access to this box, which is what proved 5a landed.**
- [x] **Step 5b: Prove it from hardware with no access to this box.** Two assertions, and the second is the one that matters:

```bash
export RESTIC_PASSWORD=... AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-005   # REQUIRED — restic defaults to us-east-1; a B2 bucket rejects it as "signature validation failed"
restic -r <repo-url> snapshots                                   # 1. can you open it at all?
# The config tree lives in the G4 "precious" snapshot, NOT `latest` (latest is the
# nightly poindexter_brain.dump stream). Find it across all snapshots:
restic -r <repo-url> find bootstrap.toml                         # -> snapshot id + path
restic -r <repo-url> restore <snap> --target /tmp/recover --include '**/bootstrap.toml'
cat "$(find /tmp/recover -name bootstrap.toml | head -1)"        # 2. is the kit itself inside?
```

Assertion 2 proves the repo is **self-sufficient**, not merely readable — that opening it hands back the master key and every service credential.

**✅ EXECUTED AND PASSED 2026-07-20 on a separate Windows PC (Git Bash), zero access to the operator box.** Path notes learned in the run, so the next person doesn't re-hit them: (1) restic's B2 URL needs the `s3:` backend prefix — a bare hostname → "invalid backend"; (2) `AWS_DEFAULT_REGION` must match the bucket (`us-east-005`), else "signature validation failed"; (3) `latest` is the DB-only nightly snapshot — use `restic find bootstrap.toml` to locate the config snapshot (id `3028292b` this run); (4) `restic dump` chokes on the Windows `C:`-style stored path from bash — `restic restore … --include '**/bootstrap.toml'` sidesteps the quoting entirely. `bootstrap.toml` restored with `database_url` + `poindexter_secret_key` present.

- [x] **Step 5c: GATE** — ✅ only when 5b has actually returned a line of `bootstrap.toml` on foreign hardware. **CLEARED 2026-07-20 — `bootstrap.toml` restored and read on a foreign PC using only the three carried secrets. The #889 circular dependency is broken: the offsite repo is now provably self-sufficient (opening it returns the master key that decrypts everything else). This clears the last non-hardware gate before Phase 7.**

> **⚠️ Verification that does NOT count.** Streaming a file back with
> `docker exec <backup-container> restic … dump …` proves the stored bytes are
> intact — it would catch a corrupt or truncated upload, and it is worth doing
> for that. It proves **nothing** about this gate, because the container supplies
> credentials that exist only on the machine whose loss is the whole scenario.
> It is the recovery equivalent of confirming you can get into your house
> because you are already standing inside it. Only a run with **no access to the
> source machine** settles this.

> **⏳ Scope note — the self-sufficiency is point-in-time, and it expires.** The
> G4 push was a **one-off**. The nightly job still streams `poindexter_brain`
> **only** — `scripts/backup-offsite/run.sh` was never taught the other tiers —
> so nothing re-pushes `bootstrap.toml`. The copy in B2 is frozen at 2026-07-19.
> Rotate a credential, add a service, or let `poindexter setup` rewrite the file,
> and the offsite copy silently drifts while still _looking_ current: `restic
snapshots` keeps reporting fresh daily snapshots, because the one database it
> covers really is fresh. **That is the failure mode to watch** — not an absent
> backup, but a self-sufficiency claim that quietly stopped being true.
>
> Re-run the `precious` push after any credential change. The real fix — teach
> the nightly runner to cover the other four databases plus the config tier, and
> assert the recovery property rather than assume it — is
> Glad-Labs/poindexter#891 (volumes are the sibling gap, #890).

---

## Phase 2 — BIOS fan-safety floor (non-destructive, light)

### Task 2.1: Set a safe BIOS fan curve

- [ ] **Step 1: Reboot into BIOS** (Del) → Q-Fan/Fan Control. Set CPU + chassis fans to a safe aggressive curve (e.g. 60% by 50 °C, 100% by 70 °C).
- [ ] **Step 2:** Note the iCUE LINK hub + XD5 pump keep their onboard curves regardless — this BIOS floor is belt-and-suspenders. Save & exit.

---

## Phase 3 — Shrink the MP600 + install Pop!\_OS alongside Windows

**Windows is not touched in this phase.** The MP700 is never written to; the only
drive modified is the MP600, and Task 3.1 makes sure it isn't holding the sole
copy of anything first. If anything here goes wrong, Windows still boots.

**Do not start unless Phase 0 = GO and Task 1.3's test-restore passed.**

### ⛔ PRE-PHASE-3 GATE — data safety before the first destructive write

Phase 3 is where the drive edits begin. Every box below must be ✅ **before**
Task 3.2 shrinks the MP600 — this is the last checkpoint where the backup is
still on a drive you are about to resize. Do not treat these as advisory; they
are the difference between "reversible" and "sole copy destroyed." (References
point back at the tasks that own each item — do them there, tick them here.)

- [ ] **G1 — Phase 0 = GO.** Both GPUs ✅ (verified 2026-07-19, driver 595.84), display at native 3440×1440 ✅ (Task 0.2 Step 4), `ping -c3 1.1.1.1` 0% loss ✅ (Task 0.2 Step 6). The GPU + drive + UEFI checks are already recorded; **display and network are the two still open** — close them first.
- [ ] **G2 — Test-restore passed (Task 1.3).** `pg_restore rc=0` on `poindexter_brain`, extensions functional (768-dim KNN returns rows), row counts match the live baseline. A backup that has never been restored is not a backup.
- [x] **G3 — Two-device rule (Task 3.1 Step 1). ✅ DONE 2026-07-19.** `C:\migration-backup-copy` on the MP700 holds 2.64 GB: both dump sets (`pg-20260718-full` — the complete 7-file set incl. `glitchtip.dump`; `pg-20260719-fresh` — newer but incomplete), the 36 `~/.poindexter` top-level config/secret files, and `~/.claude` (1053 memory `.md` files). **`bootstrap.toml` and `poindexter_brain.dump` both hash-verified against source.** Deliberately excludes the ~60 GB of regenerable bulk (superseded local DB backups, generated images, video, podcast, logs) — copying those would turn a 2 GB safety copy into an hour-long transfer that protects nothing.
- [x] **G4 — Full migration backup pushed offsite. ✅ DONE 2026-07-19.** Three snapshots in B2, all confirmed present:

  | Snapshot   | Tier             | Size     | Verification                                                                                                                       |
  | ---------- | ---------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------- |
  | `3028292b` | `precious`       | 2.64 GB  | **round-trip verified** — `bootstrap.toml` + `poindexter_brain.dump` pulled back out of B2 and hashed **byte-identical** to source |
  | `6e71efdb` | `volumes-small`  | 117 MB   | listed                                                                                                                             |
  | `c9b4eadf` | `volumes-traces` | 10.45 GB | size verified byte-exact (11,223,284,433 B)                                                                                        |

  Measured throughput ≈ 8.5 MB/s on many small files, ≈ 27 MB/s on one large one (per-file overhead dominates the small-file case). Total ≈ 12 min.

  > **Verify by restoring, not by listing.** A snapshot appearing in `restic snapshots` proves an upload happened, not that the bytes come back. Stream a file out and hash it: `restic -r <repo> dump <snap> <path> | sha256sum`, compared against the source hash. Streaming to a hash also avoids writing a secret-bearing file (`bootstrap.toml`, `globals.sql`) to disk just to check it.

  **How to push without handling any secret** (the mechanism matters — the restic password and B2 keys must never pass through a human or an agent): the running `poindexter-backup-offsite` container **already holds the credentials in its environment** and already bind-mounts `~/.poindexter/backups/auto` → `/backups`. So stage into that host directory and drive restic through `docker exec`; the container supplies its own credentials.

  ```bash
  # stage → then, from PowerShell (avoids Git Bash path mangling):
  docker exec poindexter-backup-offsite restic \
    -r "$(offsite_backup_repository from app_settings)" \
    backup /backups/_migration-offsite/<tier> --tag migration-2026-07
  ```

  **Do NOT use `docker run --env-file .poindexter-backup-offsite.env`.** That file is auto-written by `start-stack.sh` with a **CP1252 em-dash** in its header comment, so `docker run` rejects it as `invalid utf8 bytes at line 1`. Compose's parser tolerates it, which is why the stack works and only ad-hoc `docker run` trips. (Encoding bug in the writer — worth fixing separately.)

  **Tiering, because 99% of the bytes are the least valuable data.** Split into separate snapshots so the critical tier lands fast and a long tail can be interrupted without losing it:

  | Tier             | Size     | Contents                                                      |
  | ---------------- | -------- | ------------------------------------------------------------- |
  | `precious`       | 2.64 GB  | all dumps + `bootstrap.toml`/secrets + `~/.claude` memory     |
  | `volumes-small`  | 117 MB   | grafana, langfuse-minio, uptime-kuma, postiz-uploads, pgadmin |
  | `volumes-traces` | 10.45 GB | Langfuse ClickHouse — **98.9% of the volume tier by size**    |

  The ClickHouse tarball is LLM trace history: the spec classifies volumes as "useful, best-effort" and traces as re-derivable, so it is the one item here where accepting loss is defensible. Push it if bandwidth allows (≈$0.05/month on B2); do not let it block the gate.

- [x] **G5 — Fast Startup disabled (Task 3.1 Step 2–3). ✅ DONE 2026-07-19.** `powercfg /a` reports `Hibernate — Hibernation has not been enabled` and `Fast Startup — Hibernation is not available`; `C:\hiberfil.sys` **absent** (the real proof — Windows deletes it when hibernation is off); System log shows a clean `6006 → 6005` pair with **no 6008 / no 41**, i.e. no unexpected or dirty shutdown. A Linux read-write mount of a hibernated NTFS partition can corrupt it — backup included — which is why this gate exists.

  > **Two traps when verifying this.** (1) `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power\HiberbootEnabled` **stays `1`** and that is fine — Fast Startup cannot function without hibernation, and `powercfg /a` reports it unavailable. Setting that value to 0 is neither necessary nor sufficient; trust `powercfg /a` and the absence of `hiberfil.sys`, not the registry flag. (2) `fsutil dirty query D:` is the most direct check — it reads the NTFS dirty bit that Linux actually reacts to — but it **requires an elevated prompt**. Run it by hand before the first read-write mount and expect **not Dirty**.

> **Not on this gate, on purpose — #889 recovery secrets (Task 1.4 Step 5).** Stashing
> the four recovery secrets off-machine and proving `restic snapshots` from an
> unrelated box is a **Phase 7 (wipe) gate**, not a Phase 3 one — Phase 3 keeps
> Windows fully bootable as rollback, so the offsite repo being openable isn't yet
> load-bearing. Do it early anyway (it's cheap and manual), but it does not block
> the shrink. **G4 above is the Phase-3-blocking half of the backup story; Task 1.4
> Step 5 is the Phase-7-blocking half.**

### Task 3.1: Windows pre-flight (do both — each prevents a different disaster)

- [ ] **Step 1: Satisfy the two-device rule.** The MP600 currently holds the plain-dump backup, and you are about to resize it. Get the precious tier onto two devices that are **not** the MP600. It is small — ~428 MB of dumps plus ~2 GB of config:

```powershell
robocopy D:\migration-backup\pg C:\migration-backup-copy\pg /E /R:1 /W:1
robocopy D:\migration-backup\config C:\migration-backup-copy\config /E /R:1 /W:1
```

Plus the offsite push (Task 1.2 Step 3). **Expected:** `bootstrap.toml` and `poindexter_brain.dump` readable in both the MP700 copy and offsite.

- [ ] **Step 2: Disable Fast Startup.** Non-negotiable before Linux mounts NTFS — Fast Startup leaves the filesystem hibernated and dirty, and a read-write mount from Linux can corrupt it, backup included.

```powershell
powercfg /hibernate off
```

Or: **Control Panel → Power Options → Choose what the power buttons do → Change settings that are currently unavailable → uncheck "Turn on fast startup"**.

- [ ] **Step 3: Verify it took:** `powercfg /a` → **Expected:** hibernation is reported as unavailable/disabled. Then **shut down fully** (not restart) at least once before the install.

### Task 3.2: Shrink the NTFS `D:` partition

- [ ] **Step 1: Note current usage** — the MP600 is ~1863 GB with ~1706 GB free, so there is enormous headroom.
- [ ] **Step 2: Shrink from Windows** — **Disk Management → right-click `D:` → Shrink Volume**. Leave `D:` at ~**500 GB** (comfortably covers the ~156 GB in use plus backup growth) and free ~**1.3 TB** for Pop.

  > **🧱 REALITY 2026-07-22 — Windows Disk Management CANNOT do this shrink; use GParted instead.** It offered only **127 GB** of shrink space, capped by an unmovable file. Event Viewer → Application → source `Defrag` named it: **`$Mft::$BITMAP`** — the NTFS Master File Table's own metadata, sitting near the end of the volume. Windows can **never** relocate the MFT (pagefile-off, restore-points-off, shadow-copy delete all had **no effect** — it's not those). This is a hard dead-end from inside Windows. **Do it from the Pop live USB with GParted**, whose `ntfsresize` engine relocates MFT structures: select the **MP600 by model** (see the flip warning in Task 0.2 Step 5 — on this boot the MP600 is `nvme0n1`, resize `nvme0n1p2`), Resize/Move the NTFS partition to leave ~500 GB, Apply (MFT relocation is slow — don't interrupt). Safe because Fast Startup is off (G5); if GParted reports the volume "dirty", boot Windows once → `chkdsk D: /f` → full shutdown → retry.

- [ ] **Step 3: Verify** the freed space shows as _Unallocated_ (on whichever disk is the **MP600 by model**, NOT by `nvme`/Disk number — the numbering is boot-unstable, see Task 0.2 Step 5), and that `D:` still mounts and `D:\migration-backup\pg\poindexter_brain.dump` still opens. **If the shrink fails or the backup is unreadable → stop.** Windows is fine; re-copy from the Task 3.1 duplicate and reassess.

### Task 3.3: Install Pop!\_OS into the free space (Custom partitioning)

> **⛔ Do NOT choose "Clean Install."** It takes the whole selected drive and may
> adopt the MP700's existing ESP. Use **Custom (Advanced)** so Pop gets its own ESP
> on the MP600 and the MP700 is never written to.
>
> **🚨 IDENTIFY THE MP600 BY MODEL, NOT BY DISK/`nvme` NUMBER.** The `nvme0`/`nvme1`
> (and installer "Disk 0"/"Disk 1") ordering is **not stable across boots** — it
> flipped on 2026-07-22 (Task 0.2 Step 5). The MP700 is the one with the Windows
> layout (**EFI + MSR + big NTFS labelled `MP700` + Recovery**); the MP600 is the
> data drive (**MSR + big NTFS labelled `MP600`** → after the shrink, + unallocated).
> Create Pop's ESP in the MP600's unallocated space — do **not** reuse or write the
> Windows ESP that sits on the MP700.

- [ ] **Step 1: Boot the live USB → Install → Custom (Advanced).**
- [ ] **Step 2: Create this layout in the unallocated space on Disk 1 (MP600).** The split is deliberate — see Step 3.

| Mount       | Size    | FS    | Why                                                    |
| ----------- | ------- | ----- | ------------------------------------------------------ |
| `/boot/efi` | 1 GB    | FAT32 | **Pop's own ESP, on the MP600.** Flag as `esp`/boot.   |
| `swap`      | 32 GB   | swap  | Generous for 64 GB RAM without hibernation.            |
| `/`         | 150 GB  | ext4  | OS + packages only — kept deliberately small.          |
| `/data`     | ~1.1 TB | ext4  | Docker data-root, Ollama models, media, local backups. |

- [ ] **Step 3: Understand why root is small.** Everything heavy lives on `/data`, so `/` holds ~30–50 GB of pure OS. Migrating Pop to the MP700 later then becomes _reinstall root, remount `/data`_ — **the bulk data never moves**, and the MP600 lands in exactly the `/data` role the spec targets anyway. Letting Docker fill `/` instead would turn that move into relocating hundreds of GB.
- [ ] **Step 4: Confirm the installer's target summary** names the **MP600** (by model — the disk with the `MP600`-labelled NTFS, whatever its `nvme`/Disk number is this boot) for the new ESP + partitions, and lists **no changes to the MP700** (the Windows disk — EFI+MSR+`MP700` NTFS+Recovery). Read this screen carefully — it is the last checkpoint before anything is written.
- [ ] **Step 5: Install**, then complete first-boot setup (user `mattm`, timezone `America/New_York`, connect network).
- [ ] **Step 6: Verify both GPUs:** `nvidia-smi -L` → expect the 5090 and 3090 both listed.

### Task 3.4: ⛔ GATE — prove both OSes still boot

Do not proceed until this passes. Everything downstream assumes Windows is a live rollback path.

- [ ] **Step 1: Reboot → firmware boot menu (F8/F11).** **Expected:** entries for **both** Pop!\_OS and Windows Boot Manager.
- [ ] **Step 2: Boot Windows.** Confirm it reaches the desktop and `D:` still mounts with the backup intact.
- [ ] **Step 3: Boot Pop!\_OS.** Confirm desktop + network.
- [ ] **Step 4: Set the default.** Pick whichever you want the machine to land on unattended — during evaluation that should be **Pop**, since Linux will own the stack.
- [ ] **Step 5: GATE** — ✅ only when both OSes boot on demand. **If Windows will not boot, stop and repair it before going further** — without it there is no rollback and you are effectively mid-wipe.

### Task 3.5: Fix the RTC clock disagreement

Windows treats the hardware clock as local time, Linux as UTC — left alone, each skews the other on every reboot. This matters more than usual here because `operator_timezone` drives cron fire-times.

- [x] **Step 1: On Linux, make the RTC UTC** (the correct convention): `timedatectl set-local-rtc 0 --adjust-system-clock` _(done — `RTC in local TZ: no` confirmed)_
- [x] **Step 2: Tell Windows to read the RTC as UTC:**

```powershell
reg add "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f
```

- [ ] **Step 3: Verify** by booting each OS in turn and checking the clock is correct in both. **Expected:** no hour-offset drift after switching.

### Task 3.6: Bring the NVIDIA driver to a recent build

- [ ] **Step 1: Check the shipped driver:** `nvidia-smi --query-gpu=driver_version --format=csv,noheader` (Pop ships ~585). **Observed in the 2026-07-19 live session: `595.84` — already a current, 5090-capable build, so Step 2's upgrade is likely a no-op. Re-check the installed system after Task 3.3; only upgrade if the installed driver is older than the live ISO's.**
- [ ] **Step 2 (if upgrading):** `sudo apt update && sudo apt full-upgrade -y` then, if needed, install a newer System76 driver package (`system76-driver-nvidia`) or the graphics-drivers PPA build toward the 610-class you ran on Windows. Reboot.
- [ ] **Step 3: Verify:** `nvidia-smi` shows both GPUs on the new driver, no errors.

### Task 3.7: (OPTIONAL) liquidctl OCP boot one-shot — insurance, not a requirement

**Files:**

- Create: `infrastructure/systemd/liquidctl-ocp.service`

- [ ] **Step 0: Know why this is optional.** The HX1500i already retains single-rail OCP in onboard memory — neither the install nor the eventual wipe touches it. This unit exists solely to re-assert single-rail each boot as insurance against a stray bare `liquidctl initialize` (which resets to multi-rail by default). Skipping the whole task is a valid choice.
- [ ] **Step 1: Install liquidctl:** `sudo apt install -y liquidctl`.
- [ ] **Step 2: Write the unit** (repo copy):

```ini
[Unit]
Description=Corsair HX1500i single-rail +12V OCP (crash-fix, 2026-07-13)
After=multi-user.target
DefaultDependencies=no

[Service]
Type=oneshot
# initialize resets to MULTI-rail by default, so --single-12v-ocp must be
# re-applied every boot. --direct-access bypasses the corsair_psu hwmon claim.
ExecStart=/usr/bin/liquidctl initialize --single-12v-ocp --direct-access
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Install + enable:**

```bash
sudo cp infrastructure/systemd/liquidctl-ocp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now liquidctl-ocp.service
```

- [ ] **Step 4: Verify:** `systemctl status liquidctl-ocp` → `active (exited)`; `sudo liquidctl status` confirms single-rail. Reboot once and re-check it re-applies.

---

## Phase 4 — Restore + rebuild the stack

### Task 4.1: Install docker-ce + compose + nvidia-container-toolkit

- [ ] **Step 1: Install Docker Engine** per docs.docker.com (Ubuntu 24.04 repo): `docker-ce docker-ce-cli containerd.io docker-compose-plugin`. Add `mattm` to the `docker` group; `newgrp docker`.
- [ ] **Step 2: Install the container toolkit:** add NVIDIA's repo, `sudo apt install -y nvidia-container-toolkit`, `sudo nvidia-ctk runtime configure --runtime=docker`, `sudo systemctl restart docker`.
- [ ] **Step 3: Verify GPU-in-container:** `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi -L` → both GPUs listed. This is the compose GPU precondition.

### Task 4.2: Clone the repo + restore config and volumes

- [x] **Step 1: Clone:** `git clone git@github.com:Glad-Labs/glad-labs-stack.git ~/glad-labs-website` (restore the SSH/signing keys from `dot-claude`/`~/.ssh` backup first).
- [x] **Step 2: Restore operator config:** copy `config/dot-poindexter` → `~/.poindexter` and `config/dot-claude` → `~/.claude` from the MP600 backup. Confirm `~/.poindexter/bootstrap.toml` has the `localhost:5433` DSN + secrets.
- [ ] **Step 3: Restore useful volumes** into fresh docker volumes:

```bash
for t in ~/migration-backup/poindexter-backup-*/volumes/*.tar.gz; do
  v="$(basename "$t" .tar.gz)"; docker volume create "$v"
  docker run --rm -v "$v:/v" -v "$(dirname "$t"):/b" alpine tar xzf "/b/$(basename "$t")" -C /v
done
```

### Task 4.3: Ensure host.docker.internal resolves on docker-ce

**Files:**

- Modify: `docker-compose.local.yml` (add `extra_hosts` to services that reach the host — at minimum `worker`, `prometheus`, and any that use `host.docker.internal`)

- [ ] **Step 1:** For each service that reaches host Ollama/exporters, add under the service:

```yaml
extra_hosts:
  - 'host.docker.internal:host-gateway'
```

- [ ] **Step 2: Commit:**

```bash
git add docker-compose.local.yml
git commit -m "fix(migration): map host.docker.internal via host-gateway for docker-ce"
```

### Task 4.4: ⛔ THE HANDOFF — quiesce Windows and take the FINAL dump

**This is the moment ownership changes.** Everything before it was additive;
after it, Linux is the sole owner of the database and Windows must not run the
stack again without a deliberate hand-back.

**Why a fresh dump, when Phase 1's backup already restored cleanly.** That one is
a **rehearsal**, taken days or weeks ago against a _running_ stack. `pg_dump`
snapshots at the instant it **starts**, so every row written since — posts,
pipeline tasks, audit rows, cost logs, embeddings — exists only on the Windows
side. Restoring the rehearsal copy would silently roll the business back to that
date, behind a backup that looks perfectly healthy. Measured 2026-07-18:
`audit_log` gained 86 rows in the minutes between dump and verification alone.

The fix is to stop the writers _first_, so the dump has nothing to race.

> **✅ WINDOWS HALF COMPLETE — 2026-07-23 (Steps 1–7 + gate Step 9 met).** Quiesced + proven quiet (`audit_log` 203707 stable across a 35s double-sample, 0 active conns); FINAL dump at `D:\migration-backup-FINAL\poindexter-backup-20260723-013556` verified by a Windows test-restore of `poindexter_brain` (0 drift vs baseline + pgvector KNN OK — baseline persisted to `BASELINE-and-verification.txt` in that dir); `OWNER.txt=linux`; all 14 writer/resurrector tasks + `gladlabs-docker.cmd` disabled; all containers stopped. **Only Step 8 (reboot to Pop) remains, then Task 4.5.** Deviations logged: (1) surgical `docker stop`/`start` by container name instead of `compose down/up`, since the stack was already half-down; (2) extended Step 3's disable set to the 4 elevated resurrectors the `*claude*`/`*poindexter*` filter misses (`Poindexter Recovery Agent` + Watchdog, `Poindexter-DeployCheckoutSync`, `GladLabs-DR-Backup`) — needed an elevated prompt; (3) also disabled the `gladlabs-docker.cmd` Startup stack-starter (`compose up -d` on login = a split-brain vector Step 6 didn't name); (4) fixed a `backup-precious.sh` Windows `docker cp` path bug (streams `pg_dump -Fc` to the host file now).

- [ ] **Step 1: Boot Windows.** Announce downtime — the public site stays up (Vercel + R2); this pauses only the operator pipeline.
- [ ] **Step 2: Stop everything that writes**, leaving only Postgres running:

```bash
bash scripts/start-stack.sh down
docker compose -f docker-compose.local.yml up -d postgres-local
```

- [ ] **Step 3: Stop the host-side writers too.** The brain daemon and the scheduled agents connect over asyncpg and keep writing even with the stack down:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like '*claude*' -or $_.TaskName -like '*poindexter*' } |
  Disable-ScheduledTask
Get-Process | Where-Object { $_.ProcessName -match 'ollama|python' } | Format-Table Id, ProcessName
```

- [ ] **Step 4: PROVE the database is quiet.** Do not take this on faith — run both twice, ~30 s apart:

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -c \
  "select count(*) from audit_log;"
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -c \
  "select count(*) from pg_stat_activity where datname is not null and state='active' and pid <> pg_backend_pid();"
```

**Expected:** the `audit_log` count is **identical** across both runs, and active connections are `0`. If either still moves, something is still writing — find it before dumping.

- [ ] **Step 5: Take the final dump** into a _separate_ directory so it can never be confused with the rehearsal copy:

```bash
bash scripts/linux/backup-precious.sh /d/migration-backup-FINAL
```

- [ ] **Step 6: Stop Windows from ever auto-starting the stack again.** This is the mechanical half of the split-brain rule — leaving it to memory is how you end up with two live databases at 1am.
  - Docker Desktop → **Settings → General → uncheck "Start Docker Desktop when you sign in"**.
  - Leave the scheduled tasks disabled from Step 3.
- [ ] **Step 7: Record the owner** where both OSes can read it:

```powershell
"linux  $(Get-Date -Format o)  handoff at Task 4.4" | Out-File -Encoding utf8 D:\migration-backup\OWNER.txt
```

- [x] **Step 8: Reboot into Pop!\_OS.** Windows' role is now rollback-only.
- [x] **Step 9: GATE** — ✅ only when `migration-backup-FINAL` exists, Windows cannot auto-start the stack, and `OWNER.txt` reads `linux`.

### Task 4.5: Restore Postgres, then bring up the stack on identical ports

> Restore from **`/d/migration-backup-FINAL`** (Task 4.4), _not_ the Phase 1
> rehearsal copy. The NTFS partition mounts read-only or read-write from Linux —
> read-only is safer here, you only need to read.
>
> **With writers stopped, restored counts must match the Windows database
> exactly — zero drift.** Unlike the Phase 1 rehearsal, any shortfall now is a
> real failure, not a snapshot artifact. Re-run the Task 1.3 verification and
> compare against the Step 4 numbers before trusting the restore.

- [x] **Step 1: Start only Postgres** first: `bash scripts/start-stack.sh up -d postgres-local` (start-stack already reads `bootstrap.toml` and is Linux-native).
- [x] **Step 2: Restore globals + precious DBs** into the running container:

```bash
docker cp ~/migration-backup/poindexter-backup-*/pg/. poindexter-postgres-local:/tmp/pg/
docker exec poindexter-postgres-local bash -c '
  psql -U poindexter -f /tmp/pg/globals.sql || true
  for db in poindexter_brain prefect langfuse postiz; do
    createdb -U poindexter "$db" 2>/dev/null || true
    pg_restore -U poindexter -d "$db" "/tmp/pg/$db.dump" || true
  done'
```

- [x] **Step 3: Verify the restore:** `docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c "select count(*) from posts;"` → plausible count.
- [x] **Step 4: Bring up the full stack:** `bash scripts/start-stack.sh up -d`. **Expected:** all services start; `curl -s localhost:8002/api/health` → 200; `curl -s localhost:3000/api/health` (Grafana) → ok.
- [x] **Step 5: Verify the wedge class is gone:** hammer `localhost:5433` under churn: `for i in $(seq 1 40); do pg_isready -h localhost -p 5433 -U poindexter; done` → 40/40 accepting. (No `com.docker.backend` proxy exists to wedge.)

### Task 4.6: Install host-native Ollama ×2 (systemd, GPU-pinned)

**Files:**

- Create: `scripts/linux/ollama-vision.sh`
- Create: `infrastructure/systemd/ollama-primary.service`
- Create: `infrastructure/systemd/ollama-vision.service`

- [x] **Step 1: Install Ollama:** `curl -fsSL https://ollama.com/install.sh | sh`. Then **disable the packaged unit** so our two explicit units own the GPUs: `sudo systemctl disable --now ollama || true`.
- [x] **Step 2: Write the vision wrapper** (faithful port of `ollama-vision-gpu1.ps1`):

```bash
#!/usr/bin/env bash
# Pin the vision Ollama to the RTX 3090 (nvidia-smi index 1) by UUID; disable
# Vulkan (it ignores CUDA_VISIBLE_DEVICES); never unload. Serves :11435.
set -euo pipefail
uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1 | tr -d '[:space:]')"
case "$uuid" in GPU-*) ;; *) echo "ollama-vision: bad UUID '$uuid' for GPU 1 — refusing to start unpinned" >&2; exit 1;; esac
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$uuid"
export OLLAMA_VULKAN=false
export OLLAMA_HOST=0.0.0.0:11435          # 0.0.0.0 so containers reach it via host-gateway
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama/models}"
exec ollama serve
```

- [x] **Step 3: Write the primary unit** (`ollama-primary.service`):

```ini
[Unit]
Description=Ollama primary (:11434, sees both GPUs)
After=network-online.target
Wants=network-online.target

[Service]
Environment=OLLAMA_HOST=0.0.0.0:11434
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_CONTEXT_LENGTH=8192
Environment=OLLAMA_MODELS=/data/ollama/models
ExecStart=/usr/local/bin/ollama serve
Restart=on-failure
User=mattm

[Install]
WantedBy=multi-user.target
```

- [x] **Step 4: Write the vision unit** (`ollama-vision.service`):

```ini
[Unit]
Description=Ollama vision (:11435, RTX 3090 pinned, never-unload)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/home/mattm/glad-labs-website/scripts/linux/ollama-vision.sh
Restart=on-failure
User=mattm

[Install]
WantedBy=multi-user.target
```

- [x] **Step 5: Install, enable, and firewall:** _(2026-07-22: units installed + enabled as `User=mattm`; the **ufw docker-only gate is DEFERRED** — services bind `0.0.0.0`, exposed on the trusted LAN/tailnet for now)_

```bash
chmod +x scripts/linux/ollama-vision.sh
sudo cp infrastructure/systemd/ollama-primary.service infrastructure/systemd/ollama-vision.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ollama-primary ollama-vision
sudo ufw allow in on docker0 to any port 11434,11435 proto tcp && sudo ufw deny 11434,11435/tcp   # docker-only
```

- [x] **Step 6: Verify pinning:** `curl -s localhost:11434/api/tags` and `:11435/api/tags` respond; `nvidia-smi` shows the vision model resident on **GPU 1** only when loaded. Re-pull models to `/data/ollama/models` as needed.
- [x] **Step 7: Commit the artifacts:**

```bash
git add scripts/linux/ollama-vision.sh infrastructure/systemd/ollama-primary.service infrastructure/systemd/ollama-vision.service
git commit -m "feat(migration): host-native Ollama x2 systemd units (5090 :11434 / 3090 :11435)"
```

### Task 4.7: Tailscale + Claude-Code/Telegram autostart

**Files:**

- Create: `infrastructure/systemd/claude-telegram.service`

- [x] **Step 1: Tailscale:** `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --hostname=nightrider`. Verify `tailscale ip -4` returns the tailnet IP and Grafana/API are reachable over it.
- [ ] **Step 2: Install Claude Code** (native Linux binary) and re-auth; confirm `~/.claude` memory/plugins restored.
- [ ] **Step 3: Write the autostart unit** (ports `claude-telegram.cmd`):

```ini
[Unit]
Description=Claude Code with Telegram channel (operator session)
After=poindexter-stack.service network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/mattm
# Launch once the worker is healthy; adjust the claude invocation to your channel setup.
ExecStartPre=/bin/bash -c 'until curl -sf localhost:8002/api/health; do sleep 5; done'
ExecStart=/home/mattm/.local/bin/claude --telegram --dangerously-skip-permissions
Restart=on-failure
User=mattm

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Install + enable + verify** a Telegram round-trip (send a test message, get a reply).
- [ ] **Step 5: Commit** the unit.

---

## Phase 5 — Re-home orchestration

### Task 5.1: Port the scheduled agents to systemd timers

**Files:**

- Create: `scripts/linux/run-session.sh` (bash port of `claude-sessions.ps1`'s `Run-Session`)
- Create: `infrastructure/systemd/poindexter-session@.service` (template)
- Create: `scripts/linux/install-session-timers.sh` (generates the 7 timers)

- [ ] **Step 1: Write `run-session.sh`** — worktree isolation + run the ops_sessions script under the main poetry env:

```bash
#!/usr/bin/env bash
# run-session.sh <session-name> — Linux port of claude-sessions.ps1 Run-Session.
# Deterministic/local-LLM ops sessions only (the 7 enabled ones).
set -euo pipefail
NAME="${1:?session name required}"
WORK="$HOME/glad-labs-website"
MAINPKG="$WORK/src/cofounder_agent"
WT_ROOT="$HOME/.poindexter/worktrees"
LOGDIR="$HOME/.poindexter/logs/claude-sessions"
mkdir -p "$LOGDIR" "$WT_ROOT"
STAMP="$(date +%Y-%m-%d-%H%M)"
LOG="$LOGDIR/$NAME-$STAMP.log"

# Sessions that commit run in an isolated worktree off fresh origin/main.
case "$NAME" in
  codebase-audit|doc-sync|claude-md-sync|test-health) NEEDS_WT=1 ;;
  *) NEEDS_WT=0 ;;
esac

RUNDIR="$WORK"; BRANCH=""; WT=""
if [ "$NEEDS_WT" = 1 ]; then
  git -C "$WORK" worktree prune; git -C "$WORK" fetch origin --quiet
  BRANCH="auto/$NAME-$STAMP"; WT="$WT_ROOT/$NAME-$STAMP"
  git -C "$WORK" worktree add -b "$BRANCH" "$WT" origin/main >>"$LOG" 2>&1 || { echo "worktree add failed" >>"$LOG"; exit 1; }
  ln -s "$WORK/node_modules" "$WT/node_modules" 2>/dev/null || true
  RUNDIR="$WT"
fi
cleanup(){ if [ "$NEEDS_WT" = 1 ]; then rm -f "$WT/node_modules"; git -C "$WORK" worktree remove "$WT" --force || true; git -C "$WORK" branch -D "$BRANCH" || true; git -C "$WORK" worktree prune || true; fi; }
trap cleanup EXIT

SCRIPT="$RUNDIR/scripts/ops_sessions/${NAME//-/_}.py"
( cd "$MAINPKG" && poetry run python "$SCRIPT" ) >>"$LOG" 2>&1
echo "session $NAME complete" >>"$LOG"
```

- [ ] **Step 2: Write the template service** (`poindexter-session@.service`):

```ini
[Unit]
Description=Poindexter ops session %i
After=poindexter-stack.service

[Service]
Type=oneshot
ExecStart=/home/mattm/glad-labs-website/scripts/linux/run-session.sh %i
User=mattm
```

- [ ] **Step 3: Write the timer generator** (`install-session-timers.sh`) — mirrors the PS1 schedule table (`OnCalendar` in `operator_timezone`):

```bash
#!/usr/bin/env bash
set -euo pipefail
# name|OnCalendar  (local time; systemd honors the host timezone)
SCHED=(
  "dependency-review|*-*-* 06:30:00"
  "codebase-audit|Wed *-*-* 02:00:00"
  "doc-sync|Fri *-*-* 05:00:00"
  "claude-md-sync|*-*-* 02:30:00"
  "triage-sweep|Mon *-*-* 07:00:00"
  "alert-triage|*-*-* 01:00:00"
  "test-health|*-*-* 03:00:00"
)
for row in "${SCHED[@]}"; do
  name="${row%%|*}"; cal="${row#*|}"
  sudo tee "/etc/systemd/system/poindexter-session@$name.timer" >/dev/null <<EOF
[Unit]
Description=Timer for Poindexter ops session $name
[Timer]
OnCalendar=$cal
Persistent=true
[Install]
WantedBy=timers.target
EOF
  sudo systemctl enable --now "poindexter-session@$name.timer"
done
sudo systemctl daemon-reload
```

- [ ] **Step 4: Install:**

```bash
chmod +x scripts/linux/run-session.sh scripts/linux/install-session-timers.sh
sudo cp infrastructure/systemd/poindexter-session@.service /etc/systemd/system/
sudo systemctl daemon-reload
bash scripts/linux/install-session-timers.sh
```

- [ ] **Step 5: Verify:** `systemctl list-timers 'poindexter-session@*'` shows 7 timers with next-fire times. Smoke one: `sudo systemctl start poindexter-session@alert-triage` → check its log under `~/.poindexter/logs/claude-sessions/`.
- [ ] **Step 6: Commit** all three artifacts.

### Task 5.2: node_exporter + Prometheus scrape swap + alert-rule metric rename

**Files:**

- Modify: `infrastructure/prometheus/config/prometheus.yml:91-94` (the `windows` job)
- Modify: `src/cofounder_agent/services/prometheus_rule_builder.py` (host-memory rules: `windows_*` → `node_*`)

- [ ] **Step 1: Install node_exporter as a host service** bound so the container can scrape it:

```bash
sudo apt install -y prometheus-node-exporter
# bind all interfaces so the docker bridge can reach it; firewall to docker only
sudo sed -i 's/ARGS=.*/ARGS="--web.listen-address=0.0.0.0:9100"/' /etc/default/prometheus-node-exporter || true
sudo systemctl restart prometheus-node-exporter
sudo ufw allow in on docker0 to any port 9100 proto tcp && sudo ufw deny 9100/tcp
```

- [ ] **Step 2: Swap the scrape job.** Replace the `windows` job:

```yaml
# System-level metrics (CPU, RAM, disk, network) — node_exporter on host at :9100
- job_name: node
  scrape_interval: 30s
  static_configs:
    - targets: ['host.docker.internal:9100']
```

- [ ] **Step 3: Rename host-memory metrics in the DB-rendered rules.** In `prometheus_rule_builder.py`, update the `PoindexterHostMemoryLow` / `PoindexterHostMemoryThrashing` expressions:
  - `windows_memory_available_bytes` → `node_memory_MemAvailable_bytes`
  - `rate(windows_memory_swap_pages_written_total[5m])` → `rate(node_vmstat_pswpout[5m])`
- [ ] **Step 4: Reload + verify:** `curl -s localhost:9091/api/v1/targets | grep node` shows the target UP; `curl -s 'localhost:9091/api/v1/query?query=node_memory_MemAvailable_bytes'` returns data; the two host-memory rules evaluate without error.
- [ ] **Step 5: Commit** the prometheus.yml + rule-builder changes together. **Note (fast-follow, not blocking):** any Grafana panels still querying `windows_*`/`hwinfo_*`/`aida64_*` (System Health, Hardware & Power) show NoData until re-sourced to `node_*`/`lm-sensors` — track as a follow-on issue.

### Task 5.3: Add the Linux watchdog — but do NOT delete the Windows scaffolding yet

**Files:**

- Create: `scripts/linux/docker-watchdog.sh`

> **Deletions move to Phase 7.** Windows is still the rollback path for the whole
> evaluation. Removing its scripts now would mean a rollback lands on a Windows
> install whose self-healing has been gutted — the exact moment you'd need it.

- [ ] **Step 1: Add the Linux liveness check** (`scripts/linux/docker-watchdog.sh`): if `docker compose ps` shows the stack down, `sudo systemctl restart docker` + `start-stack.sh up -d`. No `wsl --shutdown` path — there is no VM to force-kill.
- [ ] **Step 2: Leave `docker-watchdog.ps1`, `idle-wsl-gpu-reset.ps1`, `register-idle-wsl-reset.ps1` and `fix-task-window-visibility.ps1` in place.** They are inert on Linux and load-bearing for rollback.
- [ ] **Step 3: Commit** the new watchdog only.

---

### Task 5.4: Port the DR backup jobs to systemd timers (the tier this plan forgot)

**Why this is its own task.** Task 5.1 ports the **ops sessions** from `claude-sessions.ps1`. The DR backup runs on a **different pair of Windows Task Scheduler jobs** that no other task in this plan touches, so porting 5.1 and declaring Phase 5 done leaves the F: backup dead. It fails the way backups always fail here: silently, with nothing reporting red.

**Files:**

- Modify: `scripts/dr-backup/run-backup.sh`, `run-hourly-pg.sh` (**in the repo as of PR #2725**)
- ~~Create~~ **✅ Committed:** `infrastructure/systemd/poindexter-dr-backup.{service,timer}`, `poindexter-dr-backup-hourly.{service,timer}` — the four units exist, shipping the same generic `poindexter` / `/home/poindexter` / `/mnt/dr-usb` placeholders as the other units. They pass `DR_RESTIC_BIN=restic` + `DR_BACKUP_REPO`/`DR_BACKUP_MOUNT` via `Environment=` and gate on `RequiresMountsFor=` so a timer never fires against an unmounted USB. **Edit the three placeholders per box before installing** (and put `User=` in the `docker` group — the scripts `docker exec` for the pg_dump).

- [x] **Step 1: Get the scripts into the repo before the wipe. ✅ DONE 2026-07-19 (PR #2725).** They previously existed only on the operator's box and inside the backups they produce — so total loss meant recovering the tooling from a restic repo whose passphrase those same scripts are what reads. Now committed at `scripts/dr-backup/`, de-identified and parameterised. **The operator-local copies at `~/.poindexter/scripts/dr-backup/` are still what actually runs** — the repo copy is behaviour-identical but nothing repoints the scheduled tasks at it. Reconciling that is Step 2.
- [ ] **Step 2: Point the Linux timers at the repo copy — no code edits needed.** The committed scripts already resolve every path from `$HOME` plus env overrides, so porting is configuration, not modification:

  | variable          | Windows default        | Linux value                                |
  | ----------------- | ---------------------- | ------------------------------------------ |
  | `DR_RESTIC_BIN`   | `$HOME/bin/restic.exe` | `restic`                                   |
  | `DR_BACKUP_REPO`  | `F:/poindexter-backup` | the USB mountpoint                         |
  | `DR_BACKUP_MOUNT` | `/f/poindexter-backup` | same as above (Git Bash needed them split) |
  | `POINDEXTER_HOME` | `$HOME/.poindexter`    | unchanged                                  |

  Prefer a UUID-keyed `/etc/fstab` mountpoint over `/media/$USER/…` — a systemd timer must not depend on a desktop session having automounted the drive. Note the `MSYS_NO_PATHCONV` guard in `backup-precious.sh` is a **no-op on Linux and should stay**; it is conditional on `$MSYSTEM`.

- [ ] **Step 3: Install the timers** — the four units are committed (daily 03:00 → `dr-daily`, hourly → `pg-hourly`, both `Persistent=true` so a missed run fires on next boot). After editing the `User=` / ExecStart path / `DR_*` + `RequiresMountsFor` placeholders:

```bash
sudo cp infrastructure/systemd/poindexter-dr-backup*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now poindexter-dr-backup.timer poindexter-dr-backup-hourly.timer
systemctl list-timers 'poindexter-dr-backup*'   # confirm both show a next-fire time
```

- [ ] **Step 4: Verify by _reading back_, not by exit code.** `restic -r <repo> snapshots --tag dr-daily` should show a new snapshot, and `restic dump <snap> <…/bootstrap.toml> | head -1` should return content. A job that exits 0 having archived nothing is the failure mode this whole plan keeps rediscovering.
- [ ] **Step 5: Disable the Windows tasks only after the Linux timers have produced a verified snapshot** — and note that **both OSes writing the same restic repo is fine** (restic locks), but both running the _hourly PG dump_ means two OSes touching the database, which the one-OS-owns-the-DB rule forbids. Disable the Windows pair at the same handoff as the stack (Task 4.4), not before and not after.

> **Do not skip the `$HOME/.claude` source path.** It was missing from `run-backup.sh` for three months and was added 2026-07-19. It is now in the committed copy, so this is safe as long as you _use_ that copy — but a re-creation from memory, or a fall-back to an older backup of the script, reintroduces the hole. Verify by reading the archive back: `restic ls <snap> | grep -c memory/` should return ~352, not 0.
>
> **And check the quoting on the `--exclude` lines.** They must be **double**-quoted now that the paths contain `$HOME`. Single quotes suppress expansion, restic then matches the literal string `$HOME/...` against nothing, the exclude silently stops applying, and several GB of superseded backups ride into every snapshot with no error. This is a one-character regression with no failure signal, and it was nearly shipped during the parameterisation in PR #2725.

---

## Phase 6 — Cutover verification, then the evaluation period

### Task 6.1: Cutover verification (the stack is genuinely working)

- [ ] **Step 1:** `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi -L` → both GPUs. ✅
- [ ] **Step 2:** Kick a real content-pipeline task end-to-end; confirm the graph_def path with the writer on the 5090 and vision on the 3090 (`nvidia-smi` during the run). ✅
- [ ] **Step 3:** `poindexter tasks list` from the host CLI + the `postgres` MCP both reach `localhost:5433` under churn — no wedge, no workaround. ✅

```bash
for i in $(seq 40); do pg_isready -h localhost -p 5433 -U poindexter; done
```

- [ ] **Step 4:** Grafana renders, alerts + brain heartbeat green, the 7 session timers listed by `systemctl list-timers`. ✅
- [ ] **Step 5:** PSU still single-rail (spot-check `sudo liquidctl status` if available); dual-GPU load test holds temps sane. ✅
- [ ] **Step 6:** Public site unaffected (spot-check gladlabs.io). ✅
- [ ] **Step 7:** Both OSes still boot; `OWNER.txt` reads `linux`; Windows cannot auto-start the stack. ✅

### Task 6.2: Run the evaluation (≥14 days) and actually measure it

The point of dual-boot is that the migration's central claim becomes **falsifiable**. Measure it rather than forming an impression.

- [ ] **Step 1: Start a dated log** at `/data/migration-eval.md`. Record the start date and the Windows baseline you're comparing against (~6 wedge events in ~3 weeks).
- [ ] **Step 2: Check weekly** — three commands, five minutes:

```bash
# Unexpected container exits / restart loops
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.RunningFor}}' | grep -iE 'exit|restart' || echo "none"

# Kernel + service errors since boot (the Linux equivalent of the KP41 hunt)
journalctl -p err -b --no-pager | tail -40

# Pipeline health from the source of truth
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc \
  "select status, count(*) from pipeline_tasks where created_at > now() - interval '7 days' group by 1 order by 2 desc;"
```

- [ ] **Step 3: Log every stability incident** with date, symptom and whether it has a Windows analogue. A Linux-specific _new_ failure class is a revert signal; a familiar one that also happened on Windows is not.
- [ ] **Step 4: Do NOT judge on speed.** The MP600 is PCIe4 QLC vs the MP700's PCIe5, and Postgres/Docker do sustained writes — Linux will likely feel slower and **that is the drive, not the OS**. If performance ends up driving the decision, re-test with Linux on the MP700 before concluding.
- [ ] **Step 5: At day 14, score the exit criteria** from the spec:
  - **Commit** if all hold: zero wedge/VHDX-class incidents · pipeline at parity nightly · both GPUs stable in containers across reboots · timers/heartbeat/alerting green · no unresolved daily-driver blocker.
  - **Revert** if any hold: a worse Linux-specific stability class · a hard hardware/driver blocker · 14 days elapsed without reaching pipeline parity.

### Task 6.3: Rollback procedure (rehearse it once, early)

Rollback is cheap **only if you've done it once**. Do a dry run in the first few days, while nothing is at stake.

- [ ] **Step 1: On Linux, quiesce and dump** — same ceremony as Task 4.4, in reverse: stop the stack, prove the DB is quiet, `backup-precious.sh /data/handback`.
- [ ] **Step 2: Copy the dump to the NTFS partition** so Windows can read it (mount `D:` read-write for this, then unmount cleanly).
- [ ] **Step 3: Reboot to Windows**, restore that dump, re-enable Docker Desktop auto-start + the scheduled tasks.
- [ ] **Step 4: Write `OWNER.txt = windows`** with a timestamp.
- [ ] **Step 5: Verify** the pipeline runs a task end-to-end on Windows.
- [ ] **Step 6: If this was the rehearsal, hand back to Linux** (Task 4.4 again) and note in the eval log that rollback is proven. **An unrehearsed rollback is a hope, not a plan.**

---

## Phase 7 — ⛔ The wipe (only after the exit criteria are met)

**This is the one irreversible phase.** Do not start it before Task 6.2 Step 5 scores a commit, the Task 1.4 credential gate is ✅, and the [poindexter#889](https://github.com/Glad-Labs/poindexter/issues/889) recovery path has been proven from an unrelated machine.

### Task 7.1: Final pre-wipe checks

- [ ] **Step 1: Confirm the offsite backup is intact and current** — list it remotely; do not assume.
- [ ] **Step 2: Confirm no Windows-only dependency remains unported.** Walk the Phase 5 list; anything still only on Windows either gets ported now or is consciously abandoned.
- [ ] **Step 3: Take a final Windows-side dump** if anything at all still lives there.

### Task 7.2: Decide the MP700's role, then wipe

- [ ] **Step 1: Choose the end state.** Recommended: **reinstall Pop on the MP700** (PCIe5 — Postgres and Docker benefit most) and demote the MP600 to `/data`. The Phase 3 layout was designed for exactly this: `/` is small and `/data` already holds the bulk, so reinstalling root is cheap and the heavy data never moves. Alternative: keep Linux on the MP600 and make the MP700 `/data` — no reinstall, but the DB stays on the slower drive.
- [ ] **Step 2: Wipe the MP700** and execute the chosen layout.
- [ ] **Step 3: Re-run Task 6.1 verification** on the final configuration.

### Task 7.3: Retire the obsolete WSL scaffolding

Now that no rollback target exists, the deletions deferred from Task 5.3 are safe.

- [ ] **Step 1: Remove the WSL-only scripts:**

```bash
git rm scripts/idle-wsl-gpu-reset.ps1 scripts/register-idle-wsl-reset.ps1 scripts/fix-task-window-visibility.ps1 scripts/docker-watchdog.ps1
```

- [ ] **Step 2: Retire the brain's `docker_port_forward_probe`** DB-wedge watch + its `alert_events` routing — dead on bare metal. Own PR, not this one.
- [ ] **Step 3: Drop** the `.wslconfig` balloon tuning, VHDX compaction runbooks, and the `#2239` autovacuum-stat-reset workaround from the docs.
- [ ] **Step 4: Commit** the deletions.

---

## Self-Review

**Spec coverage:** Phase 0 live-USB gate ✅ 0.x; Phase 1 backup (done) + credential gate + #889 recovery ✅ 1.x; Phase 2 fan floor ✅ 2.1; Phase 3 pre-flight/shrink/custom-install/both-boot gate/RTC/driver/OCP ✅ 3.1–3.7; Phase 4 docker-ce/restore/**handoff**/Ollama/Tailscale ✅ 4.1–4.7; Phase 5 timers/node_exporter/watchdog ✅ 5.x; Phase 6 cutover verification + measured evaluation + rehearsed rollback ✅ 6.1–6.3; Phase 7 wipe + deferred deletions ✅ 7.x. The spec's three governing rules each have an enforcing step: one-owner (4.4 Steps 6–7), two-device backup rule (3.1 Step 1), Fast Startup (3.1 Steps 2–3). The drive-speed confound is guarded in 6.2 Step 4 so it cannot silently decide the verdict.

**Placeholder scan:** No TBD/TODO. Hardware-specific outputs are stated as verification expectations ("two rows", "40/40 accepting", "identical across both runs") rather than fabricated exact strings — correct for an ops runbook. Deferred items (Grafana panel re-sourcing, brain-probe retirement) are explicitly non-blocking fast-follows, not hidden gaps.

**Consistency:** Ports `11434`/`11435`, GPU 0=5090/1=3090, `/data/ollama/models`, and `host.docker.internal:host-gateway` are used consistently across 4.3–4.6, 5.2 and 6.1. The `0.0.0.0`-bind rule is applied in every host-service task (Ollama 4.6, node_exporter 5.2). `migration-backup-FINAL` names the same artifact in 4.4 and 4.5; `OWNER.txt` is written in 4.4 and checked in 6.1/6.3.

**Reversibility audit** (the property this rewrite exists to protect): Phases 0–2 touch nothing. Phase 3 touches only the MP600, after 3.1 guarantees it holds no sole copy, and gates on both OSes booting. Phases 4–6 are undone by reboot + restore, rehearsed in 6.3. **Phase 7 is the only step that cannot be undone**, and it is gated on measured evidence rather than intent.
