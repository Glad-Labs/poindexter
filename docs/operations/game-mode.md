# Game mode — claiming the GPU for the operator

`poindexter game on` parks the GPU-heavy services and pauses pipeline GPU work
for a bounded window, so the box can be used for something other than
inference. `poindexter game off` ends it early; the window also expires on its
own.

## Why this exists

The operator PC is simultaneously the gaming rig and the AI inference server,
and the self-healing machinery is _designed_ to keep the inference side up. A
hand-run `docker stop` therefore does not hold: `brain/compose_drift_probe`
sees the service missing and runs `docker compose up -d` on its next cycle.

That is not a bug in the drift probe — it is the probe doing its job. Game mode
is the sanctioned way to tell it "these are down on purpose".

The incident that motivated this: a gaming session found the RTX 5090 pegged at
99% by `poindexter-speaches` (a Whisper model that had grown to 5 GB) while the
game itself failed to launch. Stopping the container by hand worked for about
60 seconds before the drift probe brought it back.

## Usage

```bash
poindexter game on                # default window (game_mode_default_hours, 4h)
poindexter game on --hours 2
poindexter game status
poindexter game off
```

`--json` on any subcommand for machine-readable output.

From a phone, via MCP: `set_game_mode(action="on", hours=4)`.

## What it does

| Effect                   | Mechanism                                                                          |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Stops the GPU containers | CLI runs `docker stop` immediately; brain keeps them down                          |
| Keeps them down          | `compose_drift_probe` folds them into its on-demand set                            |
| Frees resident VRAM      | Evicts Ollama models on **every** configured host (confirmed, not fire-and-forget) |
| Pauses pipeline GPU work | `gpu_scheduler` raises `GpuBusyError("game_mode")` on every new acquire            |
| Prevents a retry cascade | The Prefect flow stops claiming new tasks for the window                           |

CPU-only work continues: taps, research, SEO sweeps, scheduled publishing, and
anything else that never takes the GPU lock.

## Design notes

**It is a TTL, not a toggle.** `app_settings.game_mode_until` holds an absolute
ISO-8601 UTC timestamp and every consumer compares it against `now()`. A
forgotten game mode therefore expires on its own rather than silently starving
the content pipeline for days — the failure mode of a plain boolean. Nothing
has to run to clean up; an elapsed timestamp simply reads as inactive.

**Intent is central, enforcement is distributed.** `enable()` writes one key.
Three independent consumers read it, so no consumer depends on another being
reachable. This is also why triggering from a phone works: the worker container
has no docker socket, so container stopping cannot live in the service layer
and work everywhere. The CLI (which runs on the host) stops containers
immediately as an optimisation; the brain enforces it regardless.

**Restore reuses the self-healer.** `disable()` does _not_ start anything. The
drift probe already owns "a service that should be up is down" and restores the
parked services on its next cycle (≤5 min). Exactly one piece of code starts
containers.

**It does not touch `gpu_external_workload_wait_enabled`.** That flag drives the
_utilization-inference_ path in `gpu_scheduler._wait_for_gaming_clear`, which is
off by default because it mislabels the stack's own GPU bursts as gaming and
once produced a 407-second phantom pipeline stall (validation finding 4a). Game
mode is an _explicit_ signal, so it needs no heuristic and re-introduces none of
those false positives. The two are independent on purpose.

**Failure direction is deliberate.** A corrupt `game_mode_until` reads as OFF
everywhere. Failing _open_ would pause the pipeline indefinitely with nothing to
notice it; in the brain it would additionally leave real outages unrecovered and
unpaged. Both are worse than a game session seeing its containers restart.

## Settings

| Key                          | Default                                                        | Meaning                                                     |
| ---------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------- |
| `game_mode_until`            | `''`                                                           | Expiry (ISO-8601 UTC). `''` = off. Written by the adapters. |
| `game_mode_parked_services`  | `speaches,chatterbox,stable-audio,image-gen-server,wan-server` | Compose **service** names                                   |
| `game_mode_default_hours`    | `4`                                                            | Window when `--hours` is omitted                            |
| `game_mode_evict_ollama`     | `true`                                                         | Evict resident models on enable                             |
| `game_mode_container_prefix` | `poindexter-`                                                  | Prefix applied to derive container names                    |

`game_mode_parked_services` holds compose _service_ names because that is the
vocabulary `compose_drift_probe` speaks; docker container names are derived by
prefixing `game_mode_container_prefix`.

## Verifying

`poindexter game status` prints intent **and** reality:

```
game mode ON until 2026-08-10T08:17:00+00:00 (238m left)
  parked   speaches, chatterbox, stable-audio, image-gen-server, wan-server
  STILL UP poindexter-speaches — GPU is not fully free
```

A service listed as parked but still running means the stop did not land (no
docker socket, or a manual restart). Showing both stops "game mode is on" from
implying "the GPU is actually free".

Direct check:

```bash
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.free --format=csv
```

## Known gap

When triggered from MCP (no docker socket), the parked containers keep running
until the next brain cycle picks them up — GPU _admission_ pauses immediately,
but resident VRAM is not freed until then. The CLI path has no such delay.
Closing this means teaching `compose_drift_probe` to actively stop a running
parked service rather than only declining to start it.
