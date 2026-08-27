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
- **This treats the symptom.** The underlying problem is that dormant model
  sidecars park ~28 GiB in swap for nothing. Capping or idle-stopping them is
  the real headroom fix; oomd only guarantees the box stays reachable.

## Related

- The chronic-condition alert is `PoindexterHostSwapExhausted` in
  `services/prometheus_rule_builder.py` — it pages _before_ oomd has to act.
  It reads a 2 h **average**, because the raw gauge could not fire: a thrashing
  host's swap-free rattles above threshold every few minutes and reset the
  `for:` clock, leaving it `pending` for 8 h 15 m through the freeze it existed
  to catch.
- Per-container swap history lives in Prometheus (`container_memory_swap`) and
  survives a reboot — the first thing to pull when diagnosing a repeat.
- 2026-08-24 precursor incident: poindexter#1021.
