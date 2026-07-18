# Windows 11 → Pop!\_OS Full-Wipe Migration — Implementation Plan

> # ⛔ SUPERSEDED STRATEGY — DO NOT EXECUTE PHASE 3 AS WRITTEN
>
> **The full wipe was replaced by a reversible dual-boot evaluation on 2026-07-18.**
> Pop!\_OS now installs to the **MP600 alongside Windows**; the wipe is deferred to a
> final phase behind explicit exit criteria. Phase 3 below still reads
> "⛔ POINT OF NO RETURN: wipe MP700" — **that is the old strategy and following it
> would destroy the rollback path this redesign exists to preserve.**
>
> Read [`the design spec`](../specs/2026-07-17-windows-to-pop-os-migration-design.md)
> for the current strategy. This plan is being rewritten to match; until then:
>
> - **Phases 0, 1, 2 are still accurate** and safe to execute (validation, backup, BIOS floor).
> - **Task 3.0 (quiesce + final dump) is still correct** — it became the handoff ceremony.
> - **Task 3.1 onward is stale.** Do not run it.
>
> Two rules the rewrite adds, worth knowing now: only **one OS may own the database
> at a time** (never alternate — two Postgres instances diverge with no merge path),
> and the precious backup tier must live on **two devices that are not the MP600**
> before the MP600 is repartitioned.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to work this plan phase-by-phase with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking. This is an **ops/migration runbook**, not a code feature: "tests" are **verification gates** (run a command, confirm the expected output) and the destructive step (Phase 3) is guarded by a hard GO/NO-GO gate.

**Goal:** Migrate Matt's workstation from Windows 11 + Docker-Desktop/WSL2 to bare-metal Pop!\_OS 24.04, moving the whole Poindexter operator stack, both GPUs, host-native Ollama, orchestration, and hardware config, with the public site untouched.

**Architecture:** Full wipe of the MP700 (OS drive); back up to the MP600 + offsite first, restore locally after. Native `docker-ce` republishes the identical 18 host ports so `bootstrap.toml`/DSN stay zero-touch. Orchestration moves from Task Scheduler + PowerShell to `systemd`. Everything before Phase 3 is non-destructive and abortable.

**Tech Stack:** Pop!\_OS 24.04 (COSMIC), `docker-ce` + compose v2, `nvidia-container-toolkit`, native Ollama, `systemd`, `liquidctl`, `node_exporter`, Tailscale, Poindexter (Python/FastAPI/Postgres).

**Spec:** [`docs/superpowers/specs/2026-07-17-windows-to-pop-os-migration-design.md`](../specs/2026-07-17-windows-to-pop-os-migration-design.md). Read it for the _why_; this plan is the _how_.

## Global Constraints

- **Public site is decoupled** — Vercel + R2 serve gladlabs.io throughout. Never a factor in downtime.
- **Preserve the 18 published ports exactly** (`8002 3000 5433 3010 8080 9091 9093 3100 3200 4200 4040 3002 5003 18443 8001 9836 9840 7880-7882`). Same ports → host tooling + `~/.poindexter/bootstrap.toml`'s `localhost:5433` DSN need no change.
- **Native-Linux Docker networking rules (non-obvious, load-bearing):**
  - Containers reach host services via `host.docker.internal`, which on `docker-ce` requires `extra_hosts: ["host.docker.internal:host-gateway"]` (it is NOT automatic like Docker Desktop).
  - Host services that containers must reach (Ollama, node_exporter) **bind `0.0.0.0`, not `127.0.0.1`** — a loopback bind is unreachable from the docker bridge. Gate external access with `ufw` instead.
- **GPU indexing:** `nvidia-smi -i 0` = RTX 5090, `-i 1` = RTX 3090. Pin by **UUID** with `CUDA_DEVICE_ORDER=PCI_BUS_ID` (a bare numeric index is unreliable).
- **Repo lives at** `~/glad-labs-website` (clone fresh); deploy checkout + worktree roots re-created under `~/.poindexter/`.
- **Shipped `systemd` units are generic templates** (`User=poindexter`, `/home/poindexter/…`, for public-mirror hygiene). On your box either set `User=mattm` + the `/home/mattm/…` paths in each unit, or run the stack under a dedicated `poindexter` login. The `scripts/linux/*.sh` are `$HOME`-relative and need no edit. (Tasks below show the concrete `mattm` values.)
- **Everything before Phase 3 is reversible at zero cost.** Do not start Phase 3 until Phase 0 is GO and Phase 1's test-restore passed.
- **`docs/superpowers/` is stripped from the public mirror** — this plan is operator-internal.

---

## Phase 0 — Live-USB hardware validation (GO/NO-GO, zero risk)

No drive is touched. Proves the hardware runs Pop!\_OS before anything destructive.

### Task 0.1: Build the Pop!\_OS live USB

**Files:** none (external media).

- [ ] **Step 1: Download the Pop!\_OS 24.04 NVIDIA ISO** from https://system76.com/pop/download (the NVIDIA variant — ships the proprietary driver).
- [ ] **Step 2: Flash to a USB stick** (≥8 GB) with the Pop!\_OS installer's recommended tool or `dd`/balenaEtcher/Rufus from the current Windows box.
- [ ] **Step 3: Verify the ISO checksum** against the SHA-256 published on the download page. Expected: match.

### Task 0.2: Boot the live session and validate core hardware

- [ ] **Step 1: Boot the USB** (F8/F11 boot menu → the USB device) into "Try Demo Mode" — **do not** click Install.
- [ ] **Step 2: Verify both GPUs enumerate.** Open a terminal, run:
      `nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv`
      **Expected:** two rows — `0, NVIDIA GeForce RTX 5090, 32607 MiB, <ver>` and `1, NVIDIA GeForce RTX 3090, 24576 MiB, <ver>`. If either GPU is missing or `nvidia-smi` errors → **NO-GO** (driver gap; note the version, stop).
- [ ] **Step 3: Verify the display** runs the Acer ultrawide at native res: `xrandr | grep '\*'` (or COSMIC display settings) → expect `3440x1440`.
- [ ] **Step 4: Verify both NVMe drives are seen:** `lsblk -d -o NAME,MODEL,SIZE` → expect the MP700 and MP600 both listed at ~2 TB.
- [ ] **Step 5: Verify network** (Ethernet or Wi-Fi): `ping -c3 1.1.1.1` → expect 0% loss.

### Task 0.3: Confirm PSU visibility on Linux (informational — NOT a gate)

> **Resolved 2026-07-18:** the HX1500i stores the single/multi-rail toggle in its **own onboard memory**. The 2026-07-13 single-rail fix survives the wipe, iCUE's absence, and even a different machine — so this task no longer gates anything. It only confirms Linux-side telemetry.

- [ ] **Step 1: Install liquidctl in the live session:** `sudo apt update && sudo apt install -y liquidctl` (or `pipx install liquidctl` if the apt version is old).
- [ ] **Step 2: Detect the PSU:** `liquidctl list`
      **Expected:** the HX1500i appears (e.g. `Corsair HXi ... (experimental)`). If it does NOT appear → note it; the 2025 HX1500i USB ID may need a newer liquidctl (`pipx install --pip-args=-U liquidctl`). Re-test before deciding.
- [ ] **Step 3 (optional):** if you want the Linux-side re-assert available later, test `sudo liquidctl initialize --single-12v-ocp` — **always with the flag**, since a bare `initialize` resets the PSU to multi-rail.
      (If it errors that a kernel driver owns the device, retry with `--direct-access`, or `sudo modprobe -r corsair_psu` first.)
      **Expected:** initializes without error.
- [ ] **Step 4: Read it back:** `sudo liquidctl status` → note the OCP/rail fields. **Not a blocker:** the single-rail setting lives in the PSU's onboard memory and survives the wipe regardless, so a liquidctl miss here costs only optional Linux-side telemetry.

### Task 0.4: Validate iCUE LINK visibility (non-blocking)

- [ ] **Step 1:** Note that fans/pump retain their onboard curve regardless (confirmed) — this is a _nice-to-have_, not a gate.
- [ ] **Step 2 (optional):** Try OpenLinkHub per https://github.com/jurkovic-nikola/OpenLinkHub to confirm the iCUE LINK System Hub is visible for later RGB/fan control. Failure here does **not** block the migration.

**### PHASE 0 GATE:** Both GPUs ✅, display ✅, both drives ✅, network ✅. All green → **GO**. A GPU failure → **NO-GO**, Windows still fully intact. (PSU/liquidctl is informational only — the OCP setting persists in the PSU itself.)

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
- [ ] **Step 4: GATE** — confirm no account's only surviving factor is a wiped Hello passkey. This must be ✅ before Phase 3.

#### Step 5: ⛔ Break the offsite-backup circular dependency (discovered 2026-07-18)

**The automated offsite backup currently cannot be opened after a total loss.**
This is **not** a migration bug — it is true right now — but Phase 3 makes it
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

- [ ] **Step 5a: Put the recovery secrets somewhere outside both the machine and the bucket.** At minimum `offsite_backup_restic_password`, `offsite_backup_s3_access_key_id`, `offsite_backup_s3_secret_access_key`, and `poindexter_secret_key` → the Pixel's password manager (which Step 3 already re-homes) and/or printed and stored offline. **Matt only — do not automate this.**
- [ ] **Step 5b: Prove it independently.** From a machine with _no_ access to this box, use only those stored secrets to run `restic -r <repo> snapshots` and confirm the latest snapshot lists. An untested recovery path is not a recovery path.
- [ ] **Step 5c: GATE** — ✅ only when 5b has actually listed a snapshot.

> **Scope note for the migration backup.** The nightly offsite job covers
> `poindexter_brain` **only**. `bootstrap.toml`, `~/.claude` memory, the other
> four databases, and every volume exist on the MP600 copy **alone** until
> Task 1.2 Step 3 pushes them. Treat "the offsite backup is running" as covering
> one database, not the migration.

---

## Phase 2 — BIOS fan-safety floor (non-destructive, light)

### Task 2.1: Set a safe BIOS fan curve

- [ ] **Step 1: Reboot into BIOS** (Del) → Q-Fan/Fan Control. Set CPU + chassis fans to a safe aggressive curve (e.g. 60% by 50 °C, 100% by 70 °C).
- [ ] **Step 2:** Note the iCUE LINK hub + XD5 pump keep their onboard curves regardless — this BIOS floor is belt-and-suspenders. Save & exit.

---

## Phase 3 — ⛔ POINT OF NO RETURN: wipe MP700 + install Pop!\_OS

**Do not start unless Phase 0 = GO, Task 1.3 test-restore passed, Task 1.4 gate is ✅, and Task 3.0 has produced a verified FINAL dump.**

### Task 3.0: Quiesce the stack and take the FINAL dump (⛔ prevents silent data loss)

**Why this task exists.** The Phase 1 backup is a **rehearsal**, taken days or
weeks earlier against a _running_ stack. `pg_dump` snapshots at the instant it
starts, so every row written afterwards — new posts, pipeline tasks, audit rows,
cost logs, embeddings — exists only on the MP700. Wiping while the Phase 1 copy
is the newest artifact **destroys all of it**, silently, with a backup that looks
perfectly healthy. Measured on 2026-07-18: `audit_log` accumulated 86 rows in the
minutes between dump and verification alone. Across a weekend that is thousands.

The fix is to stop the writers _first_, so the dump has nothing to race.

- [ ] **Step 1: Announce downtime** (the public site stays up — this only affects the operator stack).
- [ ] **Step 2: Stop everything that writes**, leaving only Postgres running:

```bash
bash scripts/start-stack.sh down
docker compose -f docker-compose.local.yml up -d postgres-local
```

- [ ] **Step 3: Stop the host-side writers too.** The brain daemon and any scheduled agent connect over asyncpg and will keep writing even with the stack down:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like '*claude*' -or $_.TaskName -like '*poindexter*' } |
  Disable-ScheduledTask
Get-Process | Where-Object { $_.ProcessName -match 'ollama|python' } | Format-Table Id, ProcessName
```

- [ ] **Step 4: PROVE the database is quiet.** Do not take this on faith — run it twice, ~30 s apart:

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -c \
  "select count(*) from audit_log;"
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -c \
  "select count(*) from pg_stat_activity where datname is not null and state='active' and pid <> pg_backend_pid();"
```

**Expected:** the `audit_log` count is **identical** across both runs, and the active-connection count is `0`. If either still moves, something is still writing — find it before dumping.

- [ ] **Step 5: Take the final dump** into a _separate_ directory so it can never be confused with the rehearsal copy:

```bash
bash scripts/linux/backup-precious.sh /d/migration-backup-FINAL
```

- [ ] **Step 6: Re-run the Task 1.3 test-restore against THIS dump.** With writers stopped, counts must now match the live database **exactly** — zero drift. Any shortfall is a real failure, not a snapshot artifact.
- [ ] **Step 7: Push the final dump offsite** and confirm the remote listing shows the same files and sizes.
- [ ] **Step 8: GATE** — ✅ only when the final dump is verified, restored, and offsite. The MP600 must hold `migration-backup-FINAL`. **Now, and only now, proceed to the wipe.**

### Task 3.1: Install Pop!\_OS on the MP700

- [ ] **Step 1: Boot the live USB → Install.** Select **Clean Install** onto the **MP700** only. **Leave the MP600 untouched** (it holds the backup). Enable drive encryption (LUKS) if desired.
- [ ] **Step 2: Complete first-boot setup** (user `mattm`, timezone `America/New_York`, connect network).
- [ ] **Step 3: Verify both GPUs post-install:** `nvidia-smi -L` → expect both the 5090 and 3090 listed.

### Task 3.2: Bring the NVIDIA driver to a recent build

- [ ] **Step 1: Check the shipped driver:** `nvidia-smi --query-gpu=driver_version --format=csv,noheader` (Pop ships ~585).
- [ ] **Step 2 (if upgrading):** `sudo apt update && sudo apt full-upgrade -y` then, if needed, install a newer System76 driver package (`system76-driver-nvidia`) or the graphics-drivers PPA build toward the 610-class you ran on Windows. Reboot.
- [ ] **Step 3: Verify:** `nvidia-smi` shows both GPUs on the new driver, no errors.

### Task 3.3: (OPTIONAL) liquidctl OCP boot one-shot — insurance, not a requirement

**Files:**

- Create: `infrastructure/systemd/liquidctl-ocp.service`

- [ ] **Step 0: Know why this is optional.** The HX1500i already retains single-rail OCP in onboard memory — the wipe never touches it. This unit exists solely to re-assert single-rail each boot as insurance against a stray bare `liquidctl initialize` (which resets to multi-rail by default). Skipping the whole task is a valid choice.
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

- [ ] **Step 1: Clone:** `git clone git@github.com:Glad-Labs/glad-labs-stack.git ~/glad-labs-website` (restore the SSH/signing keys from `dot-claude`/`~/.ssh` backup first).
- [ ] **Step 2: Restore operator config:** copy `config/dot-poindexter` → `~/.poindexter` and `config/dot-claude` → `~/.claude` from the MP600 backup. Confirm `~/.poindexter/bootstrap.toml` has the `localhost:5433` DSN + secrets.
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

### Task 4.4: Restore Postgres, then bring up the stack on identical ports

- [ ] **Step 1: Start only Postgres** first: `bash scripts/start-stack.sh up -d postgres-local` (start-stack already reads `bootstrap.toml` and is Linux-native).
- [ ] **Step 2: Restore globals + precious DBs** into the running container:

```bash
docker cp ~/migration-backup/poindexter-backup-*/pg/. poindexter-postgres-local:/tmp/pg/
docker exec poindexter-postgres-local bash -c '
  psql -U poindexter -f /tmp/pg/globals.sql || true
  for db in poindexter_brain prefect langfuse postiz; do
    createdb -U poindexter "$db" 2>/dev/null || true
    pg_restore -U poindexter -d "$db" "/tmp/pg/$db.dump" || true
  done'
```

- [ ] **Step 3: Verify the restore:** `docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c "select count(*) from posts;"` → plausible count.
- [ ] **Step 4: Bring up the full stack:** `bash scripts/start-stack.sh up -d`. **Expected:** all services start; `curl -s localhost:8002/api/health` → 200; `curl -s localhost:3000/api/health` (Grafana) → ok.
- [ ] **Step 5: Verify the wedge class is gone:** hammer `localhost:5433` under churn: `for i in $(seq 1 40); do pg_isready -h localhost -p 5433 -U poindexter; done` → 40/40 accepting. (No `com.docker.backend` proxy exists to wedge.)

### Task 4.5: Install host-native Ollama ×2 (systemd, GPU-pinned)

**Files:**

- Create: `scripts/linux/ollama-vision.sh`
- Create: `infrastructure/systemd/ollama-primary.service`
- Create: `infrastructure/systemd/ollama-vision.service`

- [ ] **Step 1: Install Ollama:** `curl -fsSL https://ollama.com/install.sh | sh`. Then **disable the packaged unit** so our two explicit units own the GPUs: `sudo systemctl disable --now ollama || true`.
- [ ] **Step 2: Write the vision wrapper** (faithful port of `ollama-vision-gpu1.ps1`):

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

- [ ] **Step 3: Write the primary unit** (`ollama-primary.service`):

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

- [ ] **Step 4: Write the vision unit** (`ollama-vision.service`):

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

- [ ] **Step 5: Install, enable, and firewall:**

```bash
chmod +x scripts/linux/ollama-vision.sh
sudo cp infrastructure/systemd/ollama-primary.service infrastructure/systemd/ollama-vision.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ollama-primary ollama-vision
sudo ufw allow in on docker0 to any port 11434,11435 proto tcp && sudo ufw deny 11434,11435/tcp   # docker-only
```

- [ ] **Step 6: Verify pinning:** `curl -s localhost:11434/api/tags` and `:11435/api/tags` respond; `nvidia-smi` shows the vision model resident on **GPU 1** only when loaded. Re-pull models to `/data/ollama/models` as needed.
- [ ] **Step 7: Commit the artifacts:**

```bash
git add scripts/linux/ollama-vision.sh infrastructure/systemd/ollama-primary.service infrastructure/systemd/ollama-vision.service
git commit -m "feat(migration): host-native Ollama x2 systemd units (5090 :11434 / 3090 :11435)"
```

### Task 4.6: Tailscale + Claude-Code/Telegram autostart

**Files:**

- Create: `infrastructure/systemd/claude-telegram.service`

- [ ] **Step 1: Tailscale:** `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --hostname=nightrider`. Verify `tailscale ip -4` returns the tailnet IP and Grafana/API are reachable over it.
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

### Task 5.3: Delete obsolete WSL scaffolding

**Files:**

- Delete: `scripts/idle-wsl-gpu-reset.ps1`, `scripts/register-idle-wsl-reset.ps1`, `scripts/fix-task-window-visibility.ps1`
- Modify: `scripts/docker-watchdog.ps1` (or replace with a Linux `docker-watchdog.sh`)

- [ ] **Step 1: Remove the WSL-only scripts:**

```bash
git rm scripts/idle-wsl-gpu-reset.ps1 scripts/register-idle-wsl-reset.ps1 scripts/fix-task-window-visibility.ps1
```

- [ ] **Step 2: Replace the watchdog** with a minimal Linux liveness check (`scripts/linux/docker-watchdog.sh`): if `docker compose ps` shows the stack down, `sudo systemctl restart docker` + `start-stack.sh up -d`; drop the `wsl --shutdown` path entirely.
- [ ] **Step 3: Note the brain-probe retirement as a follow-on** (own PR): `docker_port_forward_probe`'s DB-wedge watch entry + `alert_events` routing are dead on bare metal — retire in a separate stack change, not the migration weekend.
- [ ] **Step 4: Commit** the deletions + watchdog replacement.

---

## Phase 6 — Verify + repurpose the MP600

### Task 6.1: Full verification (definition of done)

- [ ] **Step 1:** `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi -L` → both GPUs. ✅
- [ ] **Step 2:** Kick a real content-pipeline task end-to-end; confirm it runs on the graph_def path with the writer on the 5090 and vision on the 3090 (`nvidia-smi` during the run). ✅
- [ ] **Step 3:** `poindexter tasks list` from the host CLI + the `postgres` MCP both reach `localhost:5433` under churn — no wedge, no workaround. ✅
- [ ] **Step 4:** Grafana dashboards render, alerts + brain heartbeat green, the 7 session timers listed by `systemctl list-timers`. ✅
- [ ] **Step 5:** PSU still single-rail (persisted in PSU memory; spot-check `sudo liquidctl status` if available); dual-GPU load test holds temps sane. ✅
- [ ] **Step 6:** Public site unaffected (spot-check gladlabs.io). ✅
- [ ] **Step 7:** Every re-homed account signs in from the Linux box via the Pixel; no lockouts. ✅

### Task 6.2: Repurpose the MP600 as /data

- [ ] **Step 1: Confirm the offsite backup is intact** (you're about to wipe the local copy) — list it in Backblaze/R2.
- [ ] **Step 2: Wipe + format the MP600** (ext4), add to `/etc/fstab` mounted at `/data`.
- [ ] **Step 3: Move the Ollama model cache** to `/data/ollama/models` (matches the unit `OLLAMA_MODELS`); restart both Ollama units; confirm models resolve.
- [ ] **Step 4: Point local backups** (`db-backup-local.sh`) at `/data/backups`.

### Task 6.3: Land the migration branch

- [ ] **Step 1:** Push the branch; the PR (#2682) now carries spec + plan + all repo artifacts. Mark ready-for-review.
- [ ] **Step 2:** Merge once green (linear history). Fast-follow issues: Grafana panel re-sourcing, brain-probe retirement, remaining PowerShell utility ports.

---

## Self-Review

**Spec coverage:** Phase 0 (live-USB gate) ✅ Task 0.x; Phase 1 backup + credential gate ✅ Task 1.x; Phase 2 fan floor ✅ 2.1; Phase 3 wipe/install/driver/OCP ✅ 3.x; Phase 4 docker-ce/restore/Ollama/Tailscale/Claude ✅ 4.x; Phase 5 timers/node_exporter/deletions ✅ 5.x; Phase 6 verify/repurpose ✅ 6.x. Honest-scope items (RAM not added; PSU OCP is OS-independent and persists in the PSU's onboard memory, so it is neither fixed nor stranded by the migration) are reflected in Phase 0.3 + Task 3.3. All 7 DoD criteria map to Task 6.1.

**Placeholder scan:** No TBD/TODO. Hardware-specific outputs are described as verification expectations (e.g. "two rows", "40/40 accepting") rather than fabricated exact strings — correct for an ops runbook. The one genuinely deferred item (Grafana panel re-sourcing) is explicitly a non-blocking fast-follow, not a hidden gap.

**Consistency:** Ports `11434`/`11435`, GPU index 0=5090/1=3090, `/data/ollama/models`, and `host.docker.internal:host-gateway` are used consistently across Tasks 4.3–4.5, 5.2, and 6.2. The `0.0.0.0`-bind rule (Global Constraints) is applied in every host-service task (Ollama 4.5, node_exporter 5.2).
