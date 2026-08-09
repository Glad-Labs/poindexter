# GPU scheduler — observe layer + queue admission

The GPU scheduler (`services/gpu_scheduler.py`) serializes every stack GPU
consumer (Ollama LLM inference, image-gen, video render) behind one lock:
an in-process priority gate plus a cross-process Postgres
`pg_advisory_lock` held on a dedicated connection. This page covers the
operator-facing surfaces added by the poindexter#914 rebuild — full design
in the
[queue + admission spec](../superpowers/specs/2026-07-26-gpu-scheduler-queue-admission-design.md).

## Observe layer (P0 — always on)

Observability is unconditional — never gated by any scheduler flag.

| Surface                  | What it shows                                                                                                                                                         |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gpu_lease_stats` table  | Rolling hold-duration stats per `(owner, phase)`: samples, EWMA, streaming p50/p90 (P² estimators; fold state survives restarts). Captured on **every** lock release. |
| `gpu_queue` table        | Live cross-process mirror of waiters (contended acquires only). Rows are deleted on every wait outcome; a 1200s orphan reap covers crashes.                           |
| `GET /api/gpu/queue`     | Current holder (owner · model · held seconds), waiters, and the stats snapshot. OAuth-protected.                                                                      |
| Console GPU HUD          | "Scheduler" strip: holder line or `lock free`, up to 3 waiters, `+N more waiting`. Polls every 10s.                                                                   |
| Grafana Hardware & Power | "GPU Scheduler" row: queue-depth stat + p50/p90 hold-duration table.                                                                                                  |

The stats feed the admission ETA below — the p90 for a phase is "how long
does this kind of hold usually run", so a waiter can be told honestly
whether the current holder will be done inside its budget.

## Queue admission (P1 — opt-in per caller)

`gpu.lock()` accepts two contract kwargs:

```python
async with gpu.lock("ollama", model=..., phase=...,
                    max_wait_s=120, priority="background"):
    ...
```

- `max_wait_s` — the caller's wait budget. Before any wait, the pure
  calculator (`services/gpu_admission.py::decide`) estimates the holder's
  remaining time (`p90 − elapsed`, fallback
  `gpu_sched_eta_fallback_seconds` when the key has no stats) and checks
  VRAM fit on the pipeline GPU. A hopeless request raises `GpuBusyError`
  **immediately** — an honest skip instead of a doomed wait — and emits an
  info `gpu_admission_rejected` finding (dedup-keyed
  `owner:phase:reason`). The budget also caps the actual lock wait.
- `priority` — in-process wake order: `pipeline` > `operator` >
  `background`, FIFO within a class; a parked waiter is promoted one class
  per `gpu_sched_aging_seconds` waited, so background work can be delayed
  but never starved.

Fit math: `estimate ≤ free − headroom` grants; adding the per-card
eviction credit (`nvidia_gpu_process_memory_mib` — the resident Ollama
share on that card, **never** the `/api/ps` cross-card total) grants
after eviction; anything larger rejects `no_fit`. **Every missing
telemetry input fails OPEN** — a Prometheus blip or unknown model size
degrades to "grant", never to a false reject.

### Double inertness

1. `gpu_sched_enabled` defaults `false`.
2. A call site opts in only by passing `max_wait_s`, and the set that does
   is an **allowlist** in `tests/unit/services/test_gpu_admission_wiring.py`
   — a drive-by budget kwarg fails CI, because a budget is a behaviour
   change (work can now be skipped or refused).

So on a stock install nothing changes until both are true for a given
caller. Migration is P2 — one group per PR, sized off the soak numbers.

### P2 caller migration

| Group               | Callers                                                                                                           | Budget key (default)                         | What a refusal costs                                                                                |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1 — QA rails        | `ragas_eval`, `deepeval_rails`                                                                                    | `gpu_sched_qa_rail_max_wait_s` (45s)         | Sentinel scores + a `qa_rail_gpu_busy_skip` finding. Never blocks publish, never fabricates a pass. |
| 2 — media stages    | `generate_media_scripts`, `generate_video_shot_list`, `review_video_shot_list`                                    | `gpu_sched_media_max_wait_s` (120s)          | The post ships without that artefact + a `media_gpu_busy_skip` finding.                             |
| 3 — operator images | `services/image_service.py` (`poindexter tasks regen-image` / `add-image`, `POST /api/tasks/{id}/generate-image`) | `gpu_sched_operator_image_max_wait_s` (150s) | **Nothing is skipped** — a human gets an immediate 503 naming the holder ETA, and retries.          |

Group 3 is the odd one out and worth understanding before touching it.
Groups 1-2 are fail-soft callers whose work is genuinely optional, so a
budget converts a doomed wait into a cheap skip. Group 3 is a person
holding an open HTTP request: nothing is dropped, and the budget exists
because the **client** is already bounded
(`post_edit_regen_image_timeout_s` 300s) while the render alone can take
most of `image_render_timeout_seconds` (300s). Past ~150s of waiting the
request cannot complete regardless, so the budget only chooses between an
actionable error and a bare client timeout that throws the render away.

Each budget's default sits in the measured gap (`gpu_lease_stats`,
07-26..30): above the ordinary LLM holds the caller should simply wait
behind (`generate_content` p90 105.3s) and below the render holds it
cannot outlast (`qa_rewrite` 210.5s, `featured_image` 228.7s,
`inline_image_batch` 240.0s, `media_render` 383.5s). Setting one to `0`
restores that caller's unbounded legacy contract.

## Settings

| Key                                   | Default  | Meaning                                                                                      |
| ------------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `gpu_sched_enabled`                   | `false`  | Master switch for admission + wait-cap.                                                      |
| `gpu_sched_eta_fallback_seconds`      | `120`    | Assumed holder ETA when a key has no stats yet.                                              |
| `gpu_sched_aging_seconds`             | `300`    | Priority-class promotion window (0 = no aging).                                              |
| `gpu_sched_qa_rail_max_wait_s`        | `45`     | Wait budget for the fail-soft QA rails (P2 group 1). `0` = unbounded.                        |
| `gpu_sched_media_max_wait_s`          | `120`    | Wait budget for the media stages (P2 group 2). `0` = unbounded.                              |
| `gpu_sched_operator_image_max_wait_s` | `150`    | Wait budget for operator single-image renders (P2 group 3). `0` = unbounded.                 |
| `gpu0_headroom_gb`                    | `6`      | VRAM held back for mid-hold invisible claims (desktop transients + idle-unloaded residents). |
| `gpu_evictable_process_pattern`       | `ollama` | Substring matching the primary Ollama runner in the per-process VRAM series.                 |

Pre-existing lock tunables (`gpu_lock_acquire_timeout_seconds`,
`gpu_lock_release_timeout_seconds`, `gpu_serialize_llm_dispatch`, the
gaming-detection knobs) are unchanged.

## Per-process VRAM metric

The eviction credit needs `nvidia_gpu_process_memory_mib{gpu,pid,process}`
from the nvidia-smi exporter (`scripts/nvidia-smi-exporter.py`, container
`poindexter-gpu-exporter`). After merging an exporter change, rebuild it:

```bash
docker compose -f docker-compose.local.yml up -d --build gpu-exporter
```

Until the rebuilt exporter serves the metric, the credit reads 0.0 and
admission simply never grants on eviction — conservative, not broken.

## Multi-instance Ollama and never-unload pins (poindexter#997)

The VRAM reclaim sweeps **every** Ollama host it can reach, not just
`ollama_base_url` — per-model routing hides a second instance behind the
LiteLLM plugin's `model_api_base_overrides` (poindexter#992). That is correct
for any host that can place models on the render GPU, and wrong for one that
cannot.

The operator box runs `ollama-vision.service` on `:11435`, pinned **by UUID to
GPU 1** with `OLLAMA_KEEP_ALIVE=-1` so vision QA is never evicted:

```bash
uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1 ...)"
export CUDA_VISIBLE_DEVICES="$uuid"
export OLLAMA_KEEP_ALIVE=-1        # never unload — the whole point
```

Sweeping that instance frees **nothing** on the render card. Measured
2026-08-07: loading the model through `:11435` put **22730 MiB on GPU 1** and
moved GPU 0 by −64 MiB (desktop noise). What it does cost is the pin plus an
~18-20 GB reload across a **x4** slot before the next vision call — consistent
with the `vision_scorer_unavailable` "empty vision response" findings.

So the sweep now honours a declared pin. Ollama advertises `keep_alive=-1` as
an absurdly far-future `expires_at` (`2318-11-17T22:26:46.3314071-05:00`),
where an ordinary keep-alive lands minutes away — the operator's intent is
already in the response, so this needs no extra configuration and a
single-instance deployment is unaffected.

| Key                                    | Default | Meaning                                                                                       |
| -------------------------------------- | ------- | --------------------------------------------------------------------------------------------- |
| `ollama_unload_respect_keep_alive_pin` | `true`  | Skip models pinned never-unload. `false` restores the sweep-everything behaviour.             |
| `ollama_unload_pin_horizon_days`       | `365`   | How far out `expires_at` must sit to count as a pin. Not a knife edge — minutes vs centuries. |

Fails toward unloading: a missing or unparseable `expires_at` is swept exactly
as before, because a reclaim that silently stops working is worse than one
that evicts a model it needn't have.

> **If your pin is on the render GPU**, set `ollama_unload_respect_keep_alive_pin=false`
> — a never-unload model on the card the renderer needs cannot coexist with
> renders, and the reclaim has to win.

## Soak checklist (before P2 caller migration)

- `gpu_lease_stats` p90s look sane against known render durations.
- `/api/gpu/queue` matches reality during a busy render window.
- Zero unexpected `gpu_admission_rejected` findings (there should be none
  at all until a caller passes a budget).
