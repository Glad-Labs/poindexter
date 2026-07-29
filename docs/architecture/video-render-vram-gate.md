# Video-render VRAM gate + reclaim

**Status:** gate live (PR 1, 2026-07-12). Reclaim live (PR 2, 2026-07-12) — the idle-WSL-reset half of PR 2 ships disabled pending a supervised live run (see below). **Related:** [`video-pipeline-redesign.md`](video-pipeline-redesign.md), plan `docs/superpowers/plans/2026-07-12-video-render-vram-gate-reclaim.md`.

## Problem

The Wan 2.2 TI2V-5B hero renderer (`scripts/wan-server.py`) loads **~24 GB resident** onto `pipeline_gpu_index` — on the operator box that's the **RTX 5090, which also drives the desktop**. When stale WSL2/Docker GPU retention (`vmwp`/`vmmemWSL`) plus the desktop compositor already hold ~18 GB, the render's 24 GB pushes demand past the card's 32 GB:

- **CUDA OOM** → the render process crashes/SIGKILLs (and can leak its allocation into the WSL VM, shrinking headroom further), and
- **WDDM spills to shared system RAM** → the display compositor starves → **the desktop locks up.**

Root-cause investigation (2026-07-12): the scheduler's pre-render eviction only frees Ollama (~0.3 GB resident), never the ~18 GB actually occupying the card, so the render dispatched blind and oversubscribed. Renders succeeded only overnight, when the desktop was idle and the card was clear.

## The gate

`services/media_infra_health.py::check_media_infra_health` — already the Stage‑2 dispatch health gate for wan-server / image-gen / DNS — gains a **render‑GPU free‑VRAM preflight**:

1. `services/render_vram.py::render_gpu_free_vram_gb(site_config)` reads live free VRAM on `pipeline_gpu_index` from Prometheus: `nvidia_gpu_memory_total_mib{gpu="<idx>"} − nvidia_gpu_memory_used_mib{gpu="<idx>"}` (base URL `gpu_metrics_prometheus_url`, default `http://prometheus:9090` — the same seam the GPU scheduler already uses). Returns `None` when unreadable.
2. If free VRAM `< media_render_min_free_vram_gb` **or** unreadable, the pass is `unhealthy` (with `vram_insufficient=True`).
3. `services/jobs/dispatch_media_pipeline.py` already **defers the whole cycle** on an unhealthy pass and leaves the piece intact for the next cycle — so a render can never start unless the card has room.

**Fail-closed by design.** An unreadable VRAM reading defers rather than rendering blind — the cost of a false defer (video waits) is trivial next to the cost of a false render (desktop freeze). This mirrors the existing wan/image-gen probes, which also defer on failure.

`vram_insufficient` distinguishes a VRAM-only defer from a wan/image-gen/DNS outage, so the reclaim follow-up (PR 2) can attempt to free VRAM before deferring — a reclaim during an infra outage would be pointless.

## Settings (`settings_defaults.py`, DB-tunable)

| Key                              | Default                  | Meaning                                                                                |
| -------------------------------- | ------------------------ | -------------------------------------------------------------------------------------- |
| `media_render_vram_gate_enabled` | `true`                   | Master switch for the preflight.                                                       |
| `media_render_min_free_vram_gb`  | `25`                     | Min free VRAM on `pipeline_gpu_index` to allow a render (~24 GB model + ~1 GB margin). |
| `pipeline_gpu_index`             | `0`                      | The render/display GPU (existing key — the 5090).                                      |
| `gpu_metrics_prometheus_url`     | `http://prometheus:9090` | Prometheus base URL (existing key).                                                    |

## Observability

The **Hardware & Power** dashboard carries a dedicated **"Render VRAM gate — free (render GPU)"** stat panel (`gpu="0"` free VRAM in GB, background red below the 25 GB gate / green at or above) — the at-a-glance "is video gated right now?" signal, so a quiet video lane during desktop use reads as _expected deferral_, not a failure. The broader per-GPU trend stays on the adjacent "VRAM headroom" panel (`nvidia_gpu_memory_used_mib` / `nvidia_gpu_memory_total_mib`). A deferral is also visible as a `dispatch_media_pipeline` scheduler log `detail='… render-GPU free VRAM … < 25 GB required …'`.

> The panel's `gpu="0"` filter mirrors the `pipeline_gpu_index` default; a static dashboard can't read `app_settings`, so if you retune `pipeline_gpu_index`, update the panel query to match.

## Reclaim (PR 2, 2026-07-12)

The gate alone only _prevents_ the lockup — it doesn't free anything, so with the WSL GPU baseline high, renders would mostly just **defer forever**. PR 2 adds an active, bounded reclaim before giving up:

1. **Hard image-gen unload** — `POST /unload {"hard": true}` (`scripts/image-gen-server.py`) now unloads the pipeline _and exits the process_. `torch.cuda.empty_cache()` alone does **not** return the CUDA context/reserved pool to the host under WSL2 (confirmed 2026-07-12: soft `/unload` freed 0 GB; a container restart freed ~7 GB) — only a process exit does. Docker's `restart: unless-stopped` on `poindexter-image-gen-server` brings it back; it lazy-loads on the next `/generate`. The pre-existing soft `/unload` (no body) is unchanged — the GPU scheduler's `prepare_mode('ollama'/'idle')` callers still get the old behavior.
2. **`gpu_scheduler._unload_image_gen(hard: bool = False)`** — the scheduler's existing eviction helper gained the `hard` param; it POSTs `{"hard": true}` when set. A hard call's HTTP response commonly reads as a connection error (the process exits before uvicorn can flush the response) — that's the reclaim _working_, not a failure; logged at debug, distinct from the "server likely offline" case.
3. **`services/jobs/dispatch_media_pipeline.py::_attempt_vram_reclaim`** — when the gate's pre-dispatch probe is unhealthy **specifically because `vram_insufficient=True`** (never for a wan/image-gen/DNS outage — restarting image-gen mid-outage is pointless), the job: evicts Ollama (`gpu._unload_ollama_models()`, ~0.3 GB) → hard-unloads image-gen (~7 GB) → sleeps `media_render_reclaim_settle_seconds` (default `8`, lets the freed VRAM show up in the next Prometheus scrape) → re-probes the gate **once**. A healthy re-probe lets the cycle dispatch immediately; still-unhealthy falls through to the pre-existing defer path unchanged. Bounded to one reclaim attempt per cycle — never a retry loop.

This closes the ~7 GB image-gen slice of the deficit. The remaining ~8.6 GB stuck in WSL2's `vmwp`/`vmmemWSL` (stale Docker GPU retention that outlives any container restart) needs a host-side `wsl --shutdown` + Docker Desktop restart — that's the idle-only WSL reset below.

### The reclaim is not free — two guards (2026-07-28)

A hard unload costs image-gen a cold start plus a lazy model reload, and **any `/generate` landing in that window fails**. On the article path a failed render used to silently become a stock photo, so an over-eager reclaim degrades blog images to buy VRAM for a video render that may never happen. Two guards keep that trade honest:

1. **Server-side floor.** `/unload {"hard": true}` now refuses to exit unless at least `image_gen_hard_unload_min_reserved_mb` (default `512`) is actually **reserved**. The endpoint previously logged `torch.cuda.memory_allocated()`, which is `0` by construction immediately after `unload_pipeline()` drops the tensors — so the number it printed could never detect the thing it was trying to reclaim. `torch.cuda.memory_reserved()` is the caching-allocator pool a process exit really returns, and it is what the gate reads.
2. **Caller-side cooldown.** The per-cycle conditions were already correct (eligible work **and** a specifically-VRAM failure), but nothing remembered across cycles, so on a 5-minute cron a reclaim that _cannot_ help simply repeated forever. After a reclaim that runs and leaves the gate unhealthy, `dispatch_media_pipeline` now sits out `media_render_reclaim_cooldown_minutes` (default `30`) and emits a `vram_reclaim_ineffective` finding. A reclaim that works clears the marker immediately.

**Observed 2026-07-27** (the regression these guards close): image-gen hard-unloaded every 5 minutes for 2+ hours, each exit logging `vram_used=0 MB`, each freeing nothing, each opening a cold-start window — while the re-probe kept failing and the render never ran. Every one of those exits was pure loss, paid for in downgraded article images.

### Settings (`settings_defaults.py`)

| Key                                     | Default | Meaning                                                                                         |
| --------------------------------------- | ------- | ----------------------------------------------------------------------------------------------- |
| `media_render_reclaim_enabled`          | `true`  | Master switch for the reclaim-then-reprobe attempt.                                             |
| `media_render_reclaim_settle_seconds`   | `8`     | Delay between the reclaim and the re-probe, so Prometheus has re-scraped.                       |
| `media_render_reclaim_cooldown_minutes` | `30`    | Pause after a reclaim that left the gate unhealthy. `0` restores the old every-cycle behaviour. |
| `image_gen_hard_unload_min_reserved_mb` | `512`   | Reserved-VRAM floor below which image-gen refuses a hard unload (nothing worth the exit).       |

## Idle-only WSL/Docker reset (PR 2, host-side — build shipped, registration deferred)

The ~8.6 GB `vmwp`/`vmmemWSL` retention only returns to the host on `wsl --shutdown` + a Docker Desktop restart — a container restart doesn't touch it, and `wsl --shutdown` alone breaks GPU passthrough until Docker restarts too. The containerized worker runs _inside_ WSL and cannot reset it; this has to be a Windows host task.

`scripts/idle-wsl-gpu-reset.ps1` acts only when **all** hold: the user has been idle ≥ `idle_wsl_reset_min_idle_minutes` minutes (`GetLastInputInfo`), no `pipeline_tasks` are `in_progress`/`claimed` and no media dispatch is in flight, render-GPU free VRAM is below `idle_wsl_reset_trigger_free_vram_gb` AND GPU utilization is low (not gaming), and the last reset was ≥ `idle_wsl_reset_cooldown_hours` ago. `-DryRun` evaluates every condition and prints the decision without resetting anything.

`scripts/register-idle-wsl-reset.ps1` registers the Windows Scheduled Task — **shipped but not yet run against this machine.** Per `feedback_rebuild_authority` / the explicit-permission tier for persistent host configuration, actually registering the task (even disabled) and running the first live reset are held for a session with Matt at the keyboard: `-DryRun` gets validated against live state first, then the task registers **disabled**, then one **supervised** live reset confirms the stack bounces cleanly and self-heals before it's ever enabled to run unattended. Runbook: [`docs/operations/idle-wsl-gpu-reset.md`](../operations/idle-wsl-gpu-reset.md).

## What this does and doesn't do

- **Does:** guarantee a video render never oversubscribes the display GPU → **no more desktop freezes.** Actively reclaims the ~7 GB image-gen slice before giving up on a cycle.
- **Doesn't (yet):** touch the ~8.6 GB WSL2 retention — that needs the idle-only host reset, which is built but not yet enabled on this machine (supervised activation pending).
