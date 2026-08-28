# Host OOM protection — surviving swap exhaustion

The host runs `systemd-oomd` so that a swap-exhaustion livelock costs one
container instead of the whole machine. This page explains the failure mode it
exists for, why the obvious alternatives don't cover it, and how to verify it.

Install/repair: `sudo bash scripts/linux/install-oomd.sh` (idempotent).

## The failure mode

On **2026-08-27** the box froze hard — no I/O, no SSH, hard power cycle. It was
not an out-of-memory crash. It was a reclaim livelock:

- 60 GiB RAM, 48 GiB swap (32 GiB dm-crypt partition + 16 GiB zram).
- Four **dormant** model sidecars had parked ~28 GiB in swap doing nothing:
  chatterbox 8.9 GiB, wan-server 7.6 GiB, speaches 7.4 GiB, stable-audio
  3.9 GiB. `image-gen-server` held another 11.4 GiB resident.
- Swap sat at **0.00 GiB free** continuously from 08-26 00:25 — two days.
- Ollama kept cycling 16.1 and 20.2 GiB models (14 loads in the final 2.5 h).
  Each load needed reclaim; swap had nowhere to page to.
- The kernel spun in direct reclaim forever. The tells, in order of how loudly
  they say "livelock": the workqueue warning `swap_reclaim_work hogged CPU`
  (515 times), journald logging "Under memory pressure, flushing caches", and
  journal entries landing **3.5 minutes** after their own timestamps — the
  writes could not complete.

**The kernel OOM killer never fired**, and that is the whole point:
`MemAvailable` still read ~20 GiB, so there was never an allocation failure to
trigger it. There was no memory shortage — there was a _swap_ shortage, and the
kernel has no OOM path for that. Nothing broke the loop.

## Why not earlyoom

`earlyoom` fires only when free RAM **and** free swap are both under threshold
— an AND. This incident had 20 GiB free RAM and 0 GiB free swap, so earlyoom
would not have fired. `systemd-oomd`'s swap path keys on swap fullness **alone**
(`SwapUsedLimit`), which is exactly the failure mode. That asymmetry is the
reason for the choice; don't "simplify" it back to earlyoom later.

## The policy

| File                                                                   | Effect                                                       |
| ---------------------------------------------------------------------- | ------------------------------------------------------------ |
| `/etc/systemd/oomd.conf`                                               | `SwapUsedLimit=90%` — act well before the 99-100% livelock.  |
| `/etc/systemd/system/system.slice.d/10-oomd-swap.conf`                 | `ManagedOOMSwap=kill` — opt `system.slice` in.               |
| `/etc/systemd/system/{docker,containerd}.service.d/10-oomd-avoid.conf` | `ManagedOOMPreference=avoid` — never take the runtime first. |

Everything defaults to `ManagedOOMSwap=auto`, i.e. **opted out**. An installed,
running, enabled `systemd-oomd` with no slice opted in monitors nothing and
kills nothing. Installing the package is not the fix; the slice drop-in is.

`system.slice` is the right scope because all ~47 containers are individual
`docker-<id>.scope` units under it, and `ollama-{primary,vision}.service` live
there too — so each is a separate kill candidate rather than one aggregate.

## Verifying

Verify with `sudo oomctl`, **not** by reading the config files back — the files
being correct on disk says nothing about whether oomd parsed them. Two things
must be true:

```bash
sudo oomctl
```

- a `Swap Monitored CGroups` entry for `/system.slice`
- `Dry Run: no`

`install-oomd.sh` asserts both and exits non-zero if the policy is inert.

### Dry-running a real selection

To see what oomd _would_ kill without killing it:

```bash
sudo mkdir -p /etc/systemd/system/systemd-oomd.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/lib/systemd/systemd-oomd --dry-run\n' \
  | sudo tee /etc/systemd/system/systemd-oomd.service.d/99-TEMP-dryrun.conf
sudo systemctl daemon-reload && sudo systemctl restart systemd-oomd
sudo oomctl | head -1   # MUST print "Dry Run: yes" before you go further
```

Only once that prints `Dry Run: yes`, lower `SwapUsedLimit` below current usage
to force a selection, then read `journalctl -u systemd-oomd` for "Considered N
cgroups for killing, top candidates were:". **Remove the drop-in and restore
`SwapUsedLimit=90%` afterwards.**

> **`DryRun=yes` is not a valid `oomd.conf` key** — only the `--dry-run` CLI flag works. Setting it in the config logs `Unknown key name 'DryRun' ... ignoring` and leaves the daemon **fully armed**. Confirm `oomctl` prints `Dry Run: yes` before lowering any threshold, or the "test" kills a live container.

## Residual caveats

- **oomd ignores `oom_score_adj`.** It selects purely by cgroup swap usage, so
  the 2026-08-24 tuning (postgres `-500`, sidecars `+400`) does not steer it.
  That tuning still governs the kernel OOM killer, which remains the second
  line.
- **Postgres is a candidate.** It ranked 3rd (365 MiB) in a live dry run on a
  freshly-booted box. At the moment oomd actually acts — swap above 90% — the
  sidecars outweigh it 20-60x, and postgres is crash-safe and restarts. Even
  the worst outcome here beats a hard power cycle, which is an unclean postgres
  shutdown _plus_ everything else dying.
- **oomd treats the symptom.** It guarantees the box stays reachable; the
  cause-side fix is the sidecar RAM recycle below.

## The cause side: sidecar RAM recycle

oomd is the floor, not the fix. What actually fills swap is four **dormant**
model sidecars, so `brain/sidecar_ram_watch.py` recycles them before it gets
that far — the aim is that oomd never has to fire.

A mechanism for this already existed and did not help: `comfyui_ram_watch.py`
(poindexter#3360, added 2026-08-26) is hardcoded to `poindexter-comfyui`, and
ComfyUI held ~2 GB on 2026-08-27. **The one container being watched was not the
one filling swap.** The four that were had no watcher at all. Both probes now
run; they differ only in how they prove idleness.

|            | `comfyui_ram_watch`                | `sidecar_ram_watch`                               |
| ---------- | ---------------------------------- | ------------------------------------------------- |
| Targets    | `poindexter-comfyui`               | `sidecar_ram_recycle_targets` (CSV)               |
| Idle proof | `GET /queue`                       | GPU advisory lock free **AND** container CPU idle |
| Watermark  | `comfyui_ram_recycle_watermark_gb` | per-target, in the CSV                            |

The sidecars expose only `/health` — liveness, not idleness — which is why the
ComfyUI approach could not simply be pointed at them. The replacement gate is
`pg_advisory_lock(7777777777)`, which every GPU session in the stack holds for
its duration, plus a per-container CPU check to catch work that never took the
lock.

Both gates are re-checked immediately before the restart, **anything
unprovable counts as busy**, and at most one container is recycled per cycle
(the fattest over its watermark) to bound the blast radius. Cooldowns are
per-container.

Tuning (`app_settings`): `sidecar_ram_recycle_enabled`,
`sidecar_ram_recycle_targets` (`container:watermark_gb`, comma-separated),
`sidecar_ram_recycle_cooldown_minutes`, `sidecar_ram_recycle_cpu_idle_percent`,
`sidecar_ram_recycle_require_gpu_lock_free`. An entry without a watermark is
**skipped, not defaulted** — a typo disarms one sidecar loudly rather than
inventing a threshold nobody chose.

Every watermark must sit **below** that sidecar's real 2026-08-27 footprint,
or the probe watches the incident it was written for and does nothing. That is
not hypothetical: stable-audio was first written at 4 GB against a 3.9 GB
footprint. `test_incident_footprints_would_have_tripped` now pins each one.

Watch it work: `sidecar_ram_recycled` (info) on the Findings board per recycle,
`sidecar_ram_recycle_failed` (warn) when the restart lever itself breaks.

### If this probe goes quiet, check the GPU lock first

The GPU gate is **global**: it blocks a chatterbox recycle while ComfyUI
renders, even though those are unrelated. Sampled during a busy render window
on 2026-08-27 the lock was free only **7% of the time**, so under sustained
load — exactly when footprints grow — the probe can defer for hours. A probe
that never fires looks identical to one with nothing to do.

```bash
# a granted=true row that never clears is a STUCK GPU session, not a quiet one
psql -c "SELECT pid, granted FROM pg_locks WHERE locktype='advisory'"
```

If deferral is chronic while swap climbs, set
`sidecar_ram_recycle_require_gpu_lock_free=false`. That leaves the
per-container CPU gate doing the work alone — which is **less safe**, because a
sidecar blocked on a CUDA sync mid-inference sits under the CPU threshold and
looks idle. That is precisely why the gate defaults on; relax it deliberately,
not by habit.

## The tier problem: total swap is the wrong number

Everything above measures the swap pool as **one number**. On this host that
number hides the cliff that actually degrades the box.

Swap here is **priority-tiered**:

| device       | what it is                   | size                                  | priority |
| ------------ | ---------------------------- | ------------------------------------- | -------- |
| `/dev/zram0` | compressed pages held in RAM | 16 GiB (24 GiB after the next reboot) | 1000     |
| `/dev/dm-0`  | dm-crypt partition on NVMe   | 32 GiB                                | -1       |

Linux fills the highest priority first. So zram absorbs everything until it is
full, and from that moment **every new page lands on encrypted NVMe** — which
is what turns memory pressure into io stall.

Measured 2026-08-28: zram **99.7% full** while total swap read **61%**, memory
PSI spiking to `full avg10=8.3` and io PSI to ~10%. Both existing guards key on
the total, so both were ~14 GiB of further growth away from reacting:

- `PoindexterHostSwapExhausted` — pool free < 5%, i.e. ~45.5 GiB used
- `systemd-oomd` — `SwapUsedLimit=90%`, i.e. ~43.1 GiB used

The performance cliff is at 16 GiB, **a third of the pool**. That band is where
the box lives while feeling bad, and nothing watched it. This is the same
failure shape as the alert that missed the 2026-08-27 freeze, one layer up:
**the guard was keyed on the wrong quantity.**

### What now watches it

`nvidia-smi-exporter.py` emits per-device swap usage and zram's real RAM cost
(`host_swap_device_*`, `zram_*`). node_exporter cannot supply these — 1.7.0 has
no zram collector at all, and its `node_memory_Swap*` pair is pool-wide by
construction. The exporter reads `/proc/swaps` and `/sys/block/<dev>/mm_stat`,
both host-global, so the container needs no extra bind mount.

Rules and panels match on **`tier="fast"`**, not `device=~"zram.*"`. The
exporter derives that label from whichever device currently holds the top
priority, so resizing or replacing the fast device keeps them correct rather
than silently matching nothing.

`PoindexterHostSwapFastTierFull` (warning) fires on a 1 h mean above
`prometheus.threshold.host_swap_fast_tier_used_warning_percent` (default 90).
The average is not decoration — see the blip-reset trap in
[alert-rule-authoring.md](alert-rule-authoring.md). Panels: the **Host Memory —
pressure** row on the Hardware & Power board.

> **Adding a metric family to this exporter?** The `nvidia-smi` scrape job in
> `infrastructure/prometheus/config/prometheus.yml` has a `metric_relabel_configs`
> **allowlist**. A family missing from that regex is dropped at ingestion: the
> exporter serves it, the scrape reads `up=1`, and the series simply never
> exists. That is how the `gpu_12vhpwr_*` family shipped dark for a deploy.

## Sizing the fast tier

Config: `infrastructure/systemd/zram/pop-zram` → `/etc/pop-zram`, deployed by
`scripts/linux/install-swap-tiering.sh`. Read by `/usr/bin/pop-zram-config` at
boot.

The 2026-08-28 driver is a **leak in the `ollama-vision` runner**, measured
below. An earlier revision of this section called it "a new permanent tenant …
restarting the unit only re-deposits it" — **that was wrong, and wrong in the
direction that costs you the fix.** Recycling the runner reclaims all of it.

The tier was still sized to 24 GiB, which remains worth doing: it buys headroom
against the leak's refill time rather than against a fixed resident.

Three things to hold onto when changing this:

- **zram costs RAM.** It holds compressed pages _in memory_, so the ceiling
  costs `size / compression_ratio` — ~2.45x with zstd on this working set, so a
  full 24 GiB tier costs ~9.8 GiB. Watch "zram RAM cost" on the board. Do not
  keep raising it: zram's own memory is unswappable, so an oversized tier
  causes the pressure it was meant to relieve. 24 GiB is 40% of 60 GiB — near
  the top of the conventional band.
- **The change lands at reboot.** `pop-zram-config` opens with
  `test -b /dev/zram0 && exit 0`, so a live host keeps the zram it booted with.
  The install script says so rather than pretending otherwise.
- **A live resize is the spike you are trying to avoid.** `swapoff` faults
  every page zram holds back into RAM at once. `--apply-now` exists but refuses
  unless `MemAvailable` covers 1.5x the net cost. At boot the same change is
  free, because nothing is in zram yet.

## The ollama-vision runner leaks ~6.9 MiB per request

Measured 2026-08-28 by A/B benching the runner. The metric is **total anonymous
= `RssAnon` + `VmSwap`**, invariant to whether the kernel has swapped it out
yet — `RssAnon` alone falls to ~10 MiB within minutes and reads as "no shadow
at all", which is how this hid.

| runner                            | total anon   |
| --------------------------------- | ------------ |
| 5.5 h old, as found               | **9.35 GiB** |
| freshly loaded, mmap on (default) | **0.30 GiB** |
| freshly loaded, `use_mmap: false` | 0.46 GiB     |

Then driving plain **text** requests at a fresh runner:

| requests | total anon | Δ per 5                           |
| -------- | ---------- | --------------------------------- |
| 0        | 0.302 GiB  | —                                 |
| 5        | 0.434 GiB  | +0.132 (includes one-off warm-up) |
| 10       | 0.469 GiB  | +0.035                            |
| 15       | 0.503 GiB  | +0.034                            |
| 20       | 0.538 GiB  | +0.035                            |
| 25       | 0.572 GiB  | +0.034                            |
| 30       | 0.607 GiB  | +0.035                            |

**Dead linear at ~6.9 MiB/request with no deceleration** — a leak, not a cache
that plateaus. It never releases: the 5.5 h process sat idle 30+ minutes still
holding 9.34 GiB. 9 GiB of growth is ~1,300 requests, ≈4/min over that window,
which is ordinary QA-rail traffic. The requests were text-only, so this is
**not** specific to the vision/mmproj path.

**`use_mmap` is not the lever — do not spend time there.** llama-server's own
load log reads `mmap = true` with `CPU_Mapped model buffer size = 166.92 MiB`;
including compute buffers its intended host footprint is under 250 MiB, and the
mapped GGUF is `r--s` (shared, read-only) holding ~zero swap. Both arms of the
A/B land at 0.3-0.5 GiB. One real difference worth knowing: `mmap=on` peaks at
**18.4 GiB RSS** during load (the whole file paged through) versus 0.69 GiB for
`--no-mmap` — so every load with mmap on evicts ~18 GiB of page cache, its own
kind of pressure on a tight box, separate from the leak.

**Neither recycle probe can reach a host systemd unit** — and now that this is a
leak rather than a fixed resident, that is a real coverage gap, not a harmless
one. `sidecar_ram_watch.py` and the comfyui probe both work through
`restart_container` on docker names; `ollama-vision.service` is outside them by
construction. For host units the equivalent read is
`systemctl show <unit> -p MemorySwapCurrent`, which no container metric covers.
Recycling costs an ~85 s model reload, so the gate wants the same idle proof the
sidecar probe already uses, not a blind timer.

## Related

- The chronic-condition alert is `PoindexterHostSwapExhausted` in
  `services/prometheus_rule_builder.py` — it pages _before_ oomd has to act.
  It reads a 2 h **average**, because the raw gauge could not fire: a thrashing
  host's swap-free rattles above threshold every few minutes and reset the
  `for:` clock, leaving it `pending` for 8 h 15 m through the freeze it existed
  to catch.
- Per-container swap history lives in Prometheus (`container_memory_swap`) and
  survives a reboot — the first thing to pull when diagnosing a repeat.
- [alert-rule-authoring.md](alert-rule-authoring.md) — how to write a rule that
  can actually fire. **Both** failure modes behind this incident are documented
  there with measured numbers: the value-blip reset above, and a second one
  found afterwards — a Prometheus restart wipes pending `for:` state, and this
  host restarts it every ~1.3 h.
- 2026-08-24 precursor incident: poindexter#1021.
- Swap **tier** telemetry (`host_swap_device_*` / `zram_*`) comes from
  `scripts/nvidia-smi-exporter.py`, not node_exporter — see the tier section
  above for why, and for the scrape allowlist that will silently drop any new
  family you add to that exporter.
