# GPU scheduler: queue + admission semantics — design

**Date:** 2026-07-26
**Status:** proposed — pending Matt review
**Author:** Claude (with Matt)
**Issue:** Glad-Labs/poindexter#914

## Problem

`services/gpu_scheduler.py` is a mutex, not a scheduler. It serializes the
stack's GPU consumers (asyncio lock in-process, pg advisory lock
cross-process) with a bounded acquire (`gpu_lock_acquire_timeout_seconds`,
default 900s) that raises `GpuLockTimeoutError` on expiry. Under pressure this
produces the worst of both worlds, observed live on 2026-07-25/26:

- A waiter **burns up to 900 seconds doing nothing**, then times out, and the
  fail-soft callers (vision QA, captioner, media QA) **pass open** — the work
  is silently lost. 16 `gpu_lock_timeout` findings in one 24h window, plus the
  downstream no-ops they caused: `qa.vision` "unavailable, passing open",
  `qa_rewrite_empty_revision`, `vision_scorer_unavailable`, caption gaps.
- The VRAM fit-check (`dispatcher._clamp_num_ctx_to_budget` →
  `services/vram_budget.py`) budgets against the **summed** pool
  (`gpu_vram_total_gb=auto` ≈ 56 GB) rather than the target card's free VRAM.
  When GPU0 is genuinely full, Ollama silently part-loads models on CPU
  instead of the budget clamping — observed: the writer at `size_vram`
  11.4 GB of an 18.9 GB model, running slow with nothing alerting.
- Warm residency is unmanaged: whether a model stays loaded after a call is a
  per-service static config (Ollama keep-alive, `WHISPER__TTL`), invisible to
  the thing arbitrating GPU access.

**Operator policy this design implements (Matt, 2026-07-26):**

1. _GPU0 is load→compute→unload for everything, unless warm residency is
   earned_ (an interactive-latency justification).
2. _Things must not no-op just because they had to wait._ Waiting should
   either be short and useful, or the work should run later — not vanish.

## Non-goals

- **Dynamic cross-card model placement.** The analysis of record (#914) stands:
  with one PCIe 5.0 x16 card (5090) and one x4 Ampere card (3090), the optimal
  placement is static — writer + renders on GPU0, the pinned qwen3-vl QA lane
  warm on GPU1 (`model_api_base_overrides` → :11435, which already skips this
  lock). This design decides **when** work runs, never **where**.
- **Parallel throughput on one card.** One large workload at a time per card
  remains the rule (stability > speed). The GPU1 QA lane provides real
  parallelism _across_ cards without this scheduler's involvement.
- **A new daemon.** The scheduler stays a library inside the existing worker
  processes, coordinating through Postgres (the spinal cord), like today.
- **Replacing the pg advisory lock in phase 1.** It is proven; see
  "Cross-process priority" for the honest limitation and the phase-4 option.

## Design overview

Five additions, layered onto the existing lock rather than replacing it:

```
gpu.lock(kind, model=…, task_id=…, phase=…,
         max_wait_s=…, priority=…)          ← 1. wait contracts
        │
        ▼
  ADMISSION (new, before any waiting)
    ├─ ETA of current holder ≤ max_wait_s?  ← 2. ETA from gpu_task_sessions
    │     no → GpuBusyError(eta=…) NOW      ←    (reject in milliseconds,
    │                                             not after 900 dead seconds)
    ├─ model fits target card's free VRAM   ← 3. per-card fit + resident
    │   minus resident reserve?                  reserve (replaces pool-sum
    │     no-but-evictable → grant_after_unload   budgeting at admission)
    │     no → GpuBusyError(no_fit)
    ▼
  QUEUE (observable)                        ← 4. waiters recorded w/ position;
    in-process priority ordering                 console/Grafana panel
        │
        ▼
  HOLD  (unchanged: asyncio + pg advisory, reentrancy, unload-before-render)
        │
        ▼
  RELEASE
    ├─ duration → rolling stats (feeds ETA)
    └─ keep-alive decision                  ← 5. warmth as a scheduler
         same-model work queued/run active?      decision, not static config
           yes → keep warm   no → unload
```

A sixth piece — **deferred completion** — catches the rejects: advisory work
that couldn't run gets queued to run _late_ instead of _never_ (phase 3).

## 1. Wait contracts

`lock()` grows two optional kwargs; both default to current behaviour so every
existing call site is untouched until migrated:

```python
async with gpu.lock(
    "ollama", model=m, task_id=t, phase=p,
    max_wait_s=30,          # None (default) = legacy gpu_lock_acquire_timeout
    priority="pipeline",    # "pipeline" | "operator" | "background"
):
```

- `max_wait_s` is the caller's honest wait budget. The writer says
  `None`/large (it must run; the existing 900s ceiling still bounds it). An
  advisory rail says small (e.g. 30s) — it would rather skip-or-defer than
  stall the graph.
- `priority` orders the in-process wait queue: `pipeline` (graph nodes) >
  `operator` (console/MCP-triggered work) > `background` (scheduled jobs:
  taps, SEO, newsletter). FIFO within a class; aging promotes a
  `background` waiter after `gpu_sched_aging_seconds` so it cannot starve.

**Caller migration map** (phase 2, one PR per group):

| Callers                                                                    | Contract                                               |
| -------------------------------------------------------------------------- | ------------------------------------------------------ |
| writer / rewrite / media scripts (`dispatch_complete` from graph nodes)    | `pipeline`, `max_wait_s=None`                          |
| image atoms, media render                                                  | `pipeline`, `max_wait_s=None`                          |
| vision QA, captioner, media QA (fail-soft today)                           | `pipeline`, `max_wait_s≈30`, defer on reject (phase 3) |
| scheduled jobs (`run_taps` internal-RAG embeds, SEO, newsletter LLM calls) | `background`, `max_wait_s≈120`                         |
| operator console/MCP one-offs                                              | `operator`, `max_wait_s≈60`                            |

## 2. Admission: ETA instead of blind waiting

New typed error, distinct from the timeout:

```python
class GpuBusyError(RuntimeError):
    eta_seconds: float | None   # best estimate until the GPU frees
    reason: str                 # "eta_exceeds_budget" | "no_fit"
```

On acquire with a finite `max_wait_s`, the scheduler estimates the current
holder's remaining time and raises `GpuBusyError` **immediately** when
`eta > max_wait_s`. A rail that cannot run decides in milliseconds; the 900s
dead wait — the single biggest producer of the no-op class — disappears for
every migrated caller.

**ETA source:** `gpu_task_sessions` already records every session's duration
with owner/model/phase attribution on release (written today for
cost-attribution). The estimator keeps per-`(owner, phase)` rolling p50/p90 in
a small `gpu_lease_stats` table (upsert on release; one row per key), seeded
lazily — no history required to boot. ETA = holder's `p90 − elapsed`, floored
at 0. Unknown key → conservative fallback (`gpu_sched_eta_fallback_seconds`,
default 120). ETAs are estimates; the contract is "reject when it is _clearly_
hopeless", not precision scheduling — an ETA that proves wrong only means the
waiter waited like today.

## 3. Per-card admission (fits where it will actually run)

At admission for local-Ollama work (which lands on GPU0 — the primary
instance; the GPU1 lane bypasses this lock entirely):

```
free_gpu0 = total_gpu0 − used_gpu0 (Prometheus, via gpu_registry)
budget    = free_gpu0 − gpu0_resident_reserve_gb + evictable_ollama_vram
fits      = estimate_model_vram_gb(model, num_ctx) ≤ budget
```

- `gpu0_resident_reserve_gb` (new setting, default ~4) models what admission
  can never evict: desktop/compositor + whatever residents the operator runs.
  With the 2026-07-26 changes (voice off, speaches `WHISPER__TTL=300`,
  chatterbox → on-demand) the true resident floor is small; the reserve keeps
  the answer honest anyway.
- `evictable_ollama_vram` credits what `unload_loaded_ollama_models` can free
  (primary instance only — the pinned GPU1 instance stays exempt), so a
  fits-after-eviction case admits as `grant_after_unload` rather than
  rejecting.
- Outcome set: `grant` · `grant_after_unload` · `GpuBusyError(no_fit)`.
  `no_fit` replaces today's silent CPU part-load with an explicit signal the
  caller can act on (clamp `num_ctx` and retry, defer, or skip loudly).
- The existing pool-sum `num_ctx` clamp in the dispatcher remains as the
  pre-admission sizing pass; this check is the per-card gate it lacked.

## 4. Observable queue

- In-process: the scheduler keeps its wait-set (kind, model, phase, priority,
  enqueued_at) in memory and mirrors it to a `gpu_queue` table
  (insert-on-wait / delete-on-acquire-or-abandon) so the console and Grafana
  can show _holder + waiters + positions_ cross-process.
- Surfaces: the console GPU panel gains a "holder / waiting" strip (holder
  metadata is already recorded — `_current_owner` / `_current_model`);
  Grafana gets queue-depth + wait-time-p90 panels; `gpu_lock_timeout` and the
  new `gpu_admission_rejected` findings keep the audit trail.
- **Cross-process priority — honest limitation:** the pg advisory lock's
  waiter order is pg's, not ours; priorities reorder only within a process in
  phases 1–3. In practice ~all contending LLM/render callers live in
  `poindexter-prefect-worker`, so this covers the observed contention. If
  evidence later demands true cross-process priority, phase 4 swaps the gate
  to a lease-row claim (`FOR UPDATE SKIP LOCKED` on `gpu_queue`, holder
  heartbeat, stale-lease reap — the `service_restart_requests` pattern at
  higher frequency). Not built until needed.

## 5. Keep-alive as a scheduler decision (load→compute→unload)

On release, the scheduler — not static config — decides warmth:

- **Keep warm** when: the same model has queued waiters, or the releasing
  call's `task_id` belongs to a still-active run (the `services/task_context`
  binding from #902 makes "run active" knowable), and the model is on the
  earned-warm allowlist (`gpu_warm_models`, default: the writer).
- **Unload** otherwise: the dispatcher passes `keep_alive=0` on the _last_
  call of a run / when a different-model waiter is queued, instead of
  Ollama's default idle timer deciding.
- Renders already unload-before-acquire; unchanged.
- Non-Ollama residents opt into the same policy by their own native
  mechanisms (speaches `WHISPER__TTL` — done; chatterbox idle-unload — its
  own workstream); the scheduler documents, not micromanages, them.

Net effect: between runs GPU0 drains to the resident reserve; during a run
the writer stays hot across nodes (no reload churn); the moment a render
needs the card, eviction is explicit and credited by admission.

## 6. Deferred completion — advisory work runs late, not never (phase 3)

For callers that today catch-and-skip, a reject can instead enqueue a retry:

- New table `gpu_deferred_work` (kind, payload, not_before, attempts,
  dedup_key); a scheduler-owned drain runs when the lock frees (the release
  path pokes it) and re-invokes the registered handler.
- First tenants: `qa.vision` (re-run post-render, append its review to
  `qa_rail_reviews` with a `late: true` marker — the aggregate already
  tolerates absent rails, so a late append only ever _adds_ signal),
  `caption_images`, media QA.
- Idempotence contract: handlers must be safe to re-run (the rails append
  keyed reviews; captions overwrite by asset id). Anything that can't
  guarantee it stays skip-on-reject.
- This is also the **anticipation-pattern hook**: the same "GPU is idle"
  drain can later feed opportunistic work (embed backfill, etc.).

## Config (all app_settings, DB-first)

| Key                                         | Default                           | Meaning                                                    |
| ------------------------------------------- | --------------------------------- | ---------------------------------------------------------- |
| `gpu_sched_enabled`                         | `false` → flip after phase 1 soak | master switch; off = today's behaviour exactly             |
| `gpu_sched_eta_fallback_seconds`            | `120`                             | ETA when no stats exist for the holder's key               |
| `gpu_sched_aging_seconds`                   | `300`                             | promote a starved background waiter                        |
| `gpu0_resident_reserve_gb`                  | `4`                               | never-evictable floor on GPU0                              |
| `gpu_warm_models`                           | `""` (CSV)                        | models allowed to stay warm between calls within a run     |
| existing `gpu_lock_acquire_timeout_seconds` | `900`                             | unchanged; the outer ceiling for `max_wait_s=None` callers |

## Phasing

- **P0 — observe (behavior-neutral):** `gpu_lease_stats` capture on release,
  `gpu_queue` mirroring, console/Grafana surfaces. Ships dark behind
  `gpu_sched_enabled=false`; validates the ETA data against reality.
- **P1 — contracts + admission:** `max_wait_s`/`priority` kwargs,
  `GpuBusyError`, ETA + per-card fit checks. Callers still un-migrated →
  zero behavior change until P2.
- **P2 — caller migration:** fail-soft rails first (they convert 900s dead
  waits into instant honest skips), then background jobs, then the pipeline
  defaults. One PR per caller group, each with before/after timeout counts.
- **P3 — deferred completion + keep-alive governance:** `gpu_deferred_work` +
  drain; dispatcher keep-alive decisions; `qa.vision` late-append pilot.
- **P4 (only on evidence):** lease-table gate for cross-process priority.

## Testing

Pure-logic units for the admission calculator, ETA estimator, queue ordering
and aging (no GPU, no DB — the calculator takes numbers, returns decisions);
`integration_db` coverage for `gpu_lease_stats`/`gpu_queue`/`gpu_deferred_work`
claim-and-drain semantics (the `test_service_restart_requests` pattern);
existing lock tests keep passing untouched with `gpu_sched_enabled=false` —
that flag _is_ the regression guard.

## Success criteria

1. `gpu_lock_timeout` findings from fail-soft callers → ~0 (they reject in
   milliseconds or defer; the finding only fires for `max_wait_s=None`
   callers behind a genuinely wedged holder).
2. No silent CPU part-loads: every `size_vram < size` case is preceded by an
   explicit `no_fit` decision (clamp/defer/skip — visible in findings).
3. Advisory rails complete late rather than never: `qa.vision` coverage on
   render-heavy days rises without pipeline stalls.
4. Idle GPU0 drains to ≤ `gpu0_resident_reserve_gb` between runs.
5. Console shows holder + queue truthfully (verified against
   `gpu_task_sessions` durations).
