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

## Queue admission (P1 — ships inert)

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

### Double inertness at ship

1. `gpu_sched_enabled` defaults `false`.
2. No production call site passes `max_wait_s` yet (grep-proofed by
   `tests/unit/services/test_gpu_admission_wiring.py`).

Flipping the flag on a stock install therefore changes nothing; caller
migration is P2 — one caller group per PR, starting with the fail-soft
QA rails, informed by the soak numbers.

## Settings

| Key                              | Default  | Meaning                                                                                      |
| -------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `gpu_sched_enabled`              | `false`  | Master switch for admission + wait-cap.                                                      |
| `gpu_sched_eta_fallback_seconds` | `120`    | Assumed holder ETA when a key has no stats yet.                                              |
| `gpu_sched_aging_seconds`        | `300`    | Priority-class promotion window (0 = no aging).                                              |
| `gpu0_headroom_gb`               | `6`      | VRAM held back for mid-hold invisible claims (desktop transients + idle-unloaded residents). |
| `gpu_evictable_process_pattern`  | `ollama` | Substring matching the primary Ollama runner in the per-process VRAM series.                 |

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

## Soak checklist (before P2 caller migration)

- `gpu_lease_stats` p90s look sane against known render durations.
- `/api/gpu/queue` matches reality during a busy render window.
- Zero unexpected `gpu_admission_rejected` findings (there should be none
  at all until a caller passes a budget).
