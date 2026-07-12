# Video-render VRAM gate

**Status:** live (PR 1, 2026-07-12). **Related:** [`video-pipeline-redesign.md`](video-pipeline-redesign.md), the reclaim + idle-WSL-reset follow-up (PR 2), plan `docs/superpowers/plans/2026-07-12-video-render-vram-gate-reclaim.md`.

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

Free VRAM on `pipeline_gpu_index` is already charted on the **Hardware & Power** dashboard (`nvidia_gpu_memory_used_mib` / `nvidia_gpu_memory_total_mib`). A deferral is visible as a `dispatch_media_pipeline` scheduler log `detail='deferred — render infra unhealthy: … render-GPU free VRAM … < 25 GB required …'`.

## What this does and doesn't do

- **Does:** guarantee a video render never oversubscribes the display GPU → **no more desktop freezes.**
- **Doesn't:** free VRAM. With the desktop active and the WSL GPU baseline high, free VRAM rarely reaches 25 GB, so renders will mostly **defer** until the card is clear (fresh boot / idle). Restoring render throughput is PR 2's job: tier‑1 reclaim (hard-restart image-gen to return its ~7 GB CUDA context) + an idle-only WSL/Docker reset to clear the stubborn ~8.6 GB WSL retention.
