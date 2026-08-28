# `/data` migration onto the MP700 (PCIe5)

**Goal.** Move the 965 GB `/data` volume from the MP600 (PCIe4 QLC, shared with
`/`) onto the freshly-wiped MP700 (PCIe5), so Postgres, the Docker image store
and the containerd snapshotter run on the faster drive — and so `/data` stops
sitting at 88 %.

**Context.** Windows was removed from the MP700 on 2026-08-27 (Phase 7 of the
Pop!_OS migration). The disk is now a single ext4 volume:

|        |                                                                               |
| ------ | ----------------------------------------------------------------------------- |
| Device | `/dev/disk/by-id/nvme-Corsair_MP700_ELITE_with_Heatsink_AA0CB507000XOS-part1` |
| Label  | `poindexter-data`                                                             |
| UUID   | `95cdb50c-96fd-45c8-b069-2520a525e02a`                                        |
| Size   | 1.8 TB ext4                                                                   |

> **Address disks by `/dev/disk/by-id/` throughout.** The `nvme0`/`nvme1`
> numbering flips across boots — it has already done so once on this box. The
> serial does not move.

## Why two passes

A single stop-copy-start would mean 45–60 minutes down, almost all of it
waiting on 965 GB of bytes. Most of those bytes are not changing:
`SteamLibrary` (375 GB), `comfyui-spike` (106 GB), `hf-cache` (76 GB) and
`personal` (18 GB) are cold.

So: **pass 1 runs with the stack UP** (rsync is read-only on the source and
cannot disturb it), and **pass 2 runs with everything stopped** and transfers
only the delta. Downtime drops from ~an hour to ~10–15 minutes.

Pass 1 will copy a torn, inconsistent `docker/` and `containerd/` tree. That is
fine and expected — pass 2 re-syncs every file that changed while the daemons
were live, with `--delete` to drop anything that vanished. Nothing from pass 1
is trusted until pass 2 completes with the writers stopped.

## What must be stopped, and why

The dangerous ones are the automations that will _helpfully put things back_
while the volume is being swapped:

| Unit                                | Cadence | Why it must stop                                     |
| ----------------------------------- | ------- | ---------------------------------------------------- |
| `poindexter-docker-watchdog.timer`  | 5 min   | Runs `up -d` — will restart containers mid-migration |
| `poindexter-deploy-sync.timer`      | 10 min  | Compose-applies a fresh checkout; same hazard        |
| `poindexter-recovery-agent.service` | daemon  | Restarts services it believes are down               |
| `poindexter-session@*.timer` ×8     | various | Open DB connections mid-window                       |
| `poindexter-demo-bake.timer`        | weekly  | Long GPU job                                         |

Then the writers to `/data` itself:

| Unit                                               | Writes to             |
| -------------------------------------------------- | --------------------- |
| `docker.service`                                   | `/data/docker`        |
| `containerd.service`                               | `/data/containerd`    |
| `ollama-primary.service` / `ollama-vision.service` | `/data/ollama/models` |
| `poindexter-gpu-scraper.service`                   | (DB via API)          |
| `poindexter-mcp-http.service`                      | (DB via API)          |

`brain-daemon` is a container, so stopping Docker stops it — no separate step.
Tier 3 DR timers are already parked (2026-08-27) and need no action.

## Procedure

### 0. Pre-flight

```bash
# Target identity — must print the MP700 serial, and must NOT be the disk holding /
BYID=/dev/disk/by-id/nvme-Corsair_MP700_ELITE_with_Heatsink_AA0CB507000XOS-part1
# NOTE: SERIAL is a DISK attribute — asking a partition for it returns empty,
# which reads as "no match" rather than as an error. Resolve the parent first.
DISK=/dev/$(lsblk -no PKNAME "$(readlink -f "$BYID")" | head -1)
lsblk -dno SERIAL "$DISK"                                # expect AA0CB507000XOS
findmnt -no SOURCE /                                     # expect nvme1n1p5 (MP600)
df -h /data                                              # baseline
```

Record a baseline to compare against afterwards: container count, worker
health, Postgres row counts.

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8002/health
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc \
  "select 'posts', count(*) from posts union all
   select 'pipeline_tasks', count(*) from pipeline_tasks union all
   select 'app_settings', count(*) from app_settings;"
```

### 1. Pass 1 — bulk copy, stack still running

```bash
sudo mkdir -p /mnt/newdata
sudo mount /dev/disk/by-id/nvme-Corsair_MP700_ELITE_with_Heatsink_AA0CB507000XOS-part1 /mnt/newdata
sudo rsync -aHAX -x --numeric-ids --info=progress2 /data/ /mnt/newdata/
```

`-H` preserves hardlinks (the container image stores are full of them; without
it the copy inflates badly), `-AX` keeps ACLs and xattrs, `--numeric-ids`
avoids remapping ownership through the host's user database.

> **`-x` (`--one-file-system`) is not optional — omitting it corrupts the
> copy.** Docker keeps one live overlay mount per running container under
> `/data/docker/rootfs/overlayfs/<id>` (46 of them on this host). Without
> `-x`, rsync descends into every one and copies the _merged union view_ of
> that container's filesystem as real files, into a directory that on disk
> should hold nothing but an empty stub for Docker to mount onto.
>
> Measured on the 2026-08-28 run, where pass 1 was launched without it:
> **96 GB** of union content landed in `/mnt/newdata/docker/rootfs/overlayfs`
> against a true on-disk size of **12 KB**. It also explains a confusing
> symptom worth recognising — `du /data` reporting 1.1 TB while `df` reports
> 965 GB used. `du` crosses into the same mounts and double-counts; `df`
> measures the filesystem. **If those two disagree by roughly the size of
> your running containers, you are looking at mount traversal, not sparse
> files** (check `du --apparent-size` against `du`: equal means not sparse).
>
> Stopping Docker in §2 unmounts all 46, so pass 2 with `--delete` repairs a
> target already polluted this way — but only if `-x` is present, and only
> because the daemons are down. Do not rely on that; pass `-x` in both.

Expect ~20–30 minutes. Nothing is at risk here — the source is only read.

### 2. Quiesce

```bash
sudo systemctl stop poindexter-docker-watchdog.timer poindexter-deploy-sync.timer \
     poindexter-demo-bake.timer 'poindexter-session@*.timer'
sudo systemctl stop poindexter-recovery-agent.service poindexter-mcp-http.service \
     poindexter-gpu-scraper.service
docker compose -f /home/mattm/.poindexter/deploy/glad-labs-stack/docker-compose.local.yml stop
sudo systemctl stop ollama-primary.service ollama-vision.service
sudo systemctl stop docker.socket docker.service containerd.service
```

Confirm nothing still holds the volume:

```bash
sudo fuser -vm /data 2>&1 | head       # expect no processes
sudo lsof +D /data 2>/dev/null | head  # expect empty
```

### 3. Pass 2 — delta copy with everything stopped

```bash
sudo rsync -aHAX -x --numeric-ids --delete --info=progress2 /data/ /mnt/newdata/
```

`--delete` matters: pass 1 copied files that the daemons have since removed,
and a stale layer left behind in the image store is exactly the kind of thing
that produces a weird failure three days later.

### 4. Verify before committing

```bash
# Byte-level: any output at all is a mismatch
sudo rsync -aHAXn -x --numeric-ids --delete --itemize-changes /data/ /mnt/newdata/ | head -20

# Structural
sudo find /data -xdev | wc -l ; sudo find /mnt/newdata -xdev | wc -l
sudo du -sb /data /mnt/newdata
```

Counts and byte totals must match, and the itemize pass must print nothing.
**Do not proceed on a green exit code alone** — an empty itemize list is the
actual evidence.

### 5. Swap

```bash
sudo cp /etc/fstab /etc/fstab.bak-pre-mp700-$(date -u +%Y%m%dT%H%M%SZ)
sudo umount /mnt/newdata
sudo umount /data
# Replace the /data UUID with the MP700's
sudo sed -i 's|^UUID=c8c2a2de-c13a-437e-ac79-0b550b9b7c4c |UUID=95cdb50c-96fd-45c8-b069-2520a525e02a |' /etc/fstab
sudo mount /data
findmnt -no SOURCE,UUID /data   # expect nvme0n1p1 / 95cdb50c-...
```

### 6. Restart and verify

```bash
sudo systemctl start containerd.service docker.service
sudo systemctl start ollama-primary.service ollama-vision.service
cd /home/mattm/.poindexter/deploy/glad-labs-stack && bash scripts/start-stack.sh up -d
```

Then re-run the §0 baseline commands. Postgres row counts must match exactly,
container count must match, worker health must be 200.

Re-arm automation only once that passes:

```bash
sudo systemctl start poindexter-recovery-agent.service poindexter-mcp-http.service \
     poindexter-gpu-scraper.service
sudo systemctl start poindexter-docker-watchdog.timer poindexter-deploy-sync.timer \
     poindexter-demo-bake.timer 'poindexter-session@*.timer'
```

### 7. Reclaim — a separate decision, later

**Do not wipe the old partition in this window.** Leave `nvme1n1p6` intact and
untouched; it is the rollback. Once the stack has run a full day on the new
volume — including a backup cycle and a content-pipeline run — the old
partition can be reclaimed and merged with the idle 500 GB NTFS
(`nvme1n1p2`, the ex-Windows `D:`) into free space on the MP600.

## Rollback

Cheap at every step until §7, because the source is never modified.

- **Failure during pass 1 or 2** — nothing has changed; unmount `/mnt/newdata`
  and restart the stack.
- **Failure after the fstab swap** — restore and remount the original:

```bash
sudo umount /data
sudo cp /etc/fstab.bak-pre-mp700-<stamp> /etc/fstab
sudo mount /data
findmnt -no SOURCE /data   # expect nvme1n1p6 again
```

- **Stack misbehaves on the new volume** — same rollback; the MP600 copy is
  byte-identical and untouched.

## Risks

| Risk                                                 | Mitigation                                 |
| ---------------------------------------------------- | ------------------------------------------ |
| Watchdog/deploy-sync restarts containers mid-swap    | Timers stopped in §2, re-armed only in §6  |
| Hardlinks in the image stores inflate the copy       | `-H` on both passes                        |
| Ownership remapped through the host user db          | `--numeric-ids`                            |
| Stale files from pass 1 survive                      | `--delete` on pass 2                       |
| Silent corruption                                    | Itemize verification in §4, not exit codes |
| Wrong disk targeted after a reboot renumbers devices | `by-id` paths throughout                   |
| Old data deleted too early                           | §7 explicitly deferred                     |
