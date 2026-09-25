# GPU scheduler — observe layer + queue admission

The GPU scheduler (`services/gpu_scheduler.py`) serializes every stack GPU
consumer (Ollama LLM inference, image-gen, video render) behind one lock:
an in-process priority gate plus a cross-process Postgres
`pg_advisory_lock` held on a dedicated connection. This page covers the
operator-facing surfaces added by the poindexter#914 rebuild — full design
in the
queue + admission spec (`docs/superpowers/specs/2026-07-26-gpu-scheduler-queue-admission-design.md`).

## Observe layer (P0 — always on)

Observability is unconditional — never gated by any scheduler flag.

| Surface                         | What it shows                                                                                                                                                                                                                                                    |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gpu_lease_stats` table         | Rolling hold-duration stats per `(owner, phase)`: samples, EWMA, streaming p50/p90 (P² estimators; fold state survives restarts). Captured on **every** lock release.                                                                                            |
| `gpu_queue` table               | Live cross-process mirror of waiters. A wait is recorded once it outlives `gpu_queue_mirror_delay_seconds`, whichever stage it is parked at. Rows are deleted on every wait outcome; a 1200s orphan reap covers crashes.                                         |
| `pg_locks` / `pg_stat_activity` | The **holder**, cross-process. The lock sits on a connection stamped `poindexter-gpu:<owner>:<phase>[:<task>]:pid<N>`, so any process can name it (`gpu_scheduler.list_pg_holders`). No holder table exists, by design — the lock's own lifecycle is the record. |
| `GET /api/gpu/queue`            | `holders[]` (owner · phase · task · held seconds · keys), `holder` as the head of that list, waiters, and the stats snapshot. OAuth-protected.                                                                                                                   |
| Console GPU HUD                 | "Scheduler" strip: holder line(s) or `lock free`, up to 3 waiters, `+N more waiting`. Polls every 10s.                                                                                                                                                           |
| Grafana Hardware & Power        | "GPU Scheduler" row: queue-depth stat + p50/p90 hold-duration table.                                                                                                                                                                                             |

The stats feed the admission ETA below — the p90 for a phase is "how long
does this kind of hold usually run", so a waiter can be told honestly
whether the current holder will be done inside its budget.

### Why the holder comes from Postgres

Both halves of that panel once answered at different scopes, and the result
read as a contradiction: waiters from the DB (cross-process) printed beneath a
holder line from `gpu._current_owner` (a module global in whichever process
served the request). The console runs in `poindexter-worker`; the pipeline's
GPU work runs in `poindexter-prefect-worker`. So a render could hold the card
for 20 minutes, queue three callers behind it, and the panel would say **`lock
free · nothing holding the GPU`** directly above them.

Three mechanisms produced it, and all three are closed:

1. **The holder was process-local.** Now resolved from `pg_locks` joined to
   `pg_stat_activity`. Every holder — scoped or not — takes the base key
   (`7777777777`), exclusively when unscoped and shared when device-scoped, so
   one filter on that key enumerates them all without knowing the unbounded
   set of device keys.
2. **A gate can be held while `_current_owner` is still `None`.** `lock()`
   takes the in-process gates, _then_ blocks on `pg_advisory_lock`, and sets
   `_current_owner` only after both succeed. For the whole pg wait — up to the
   900s ceiling — later callers queue behind a holder that names itself
   nowhere. That window is why the `in_process`-stage timeout used to print
   `waiting for in-process holder None (None)`, the exact phrasing
   poindexter#1018 set out to kill (it had only been wired to the
   `pg_advisory` stage). Both stages now ask Postgres.
3. **The waiter mirror was stage-dependent.** It recorded only waits queued
   behind an in-process holder, on the premise that "all observed contention
   is in-process within prefect-worker". Device scoping retired that premise:
   a caller blocked at the pg stage behind another container appeared in no
   waiter list at all. Visibility now keys off _time waited_, not _where the
   wait is parked_ — which is also what keeps the uncontended path zero-I/O.

`holder.source` says which answer you are reading: `postgres` is the
cross-process truth, `in_process` is the fallback used only when Postgres
named nobody (it can only see one container, and the console labels it
`this process only`). A holder whose connection carries no tag is still
reported, as `unknown` with its backend pid — "held by someone who won't say
who" is a different fact from "free", and collapsing the two was the bug.

Admission's ETA gate kept the same process-local view after the console
moved to Postgres (stack#3974). It reads the Postgres holders too now; see
[Which holder admission weighs](#which-holder-admission-weighs).

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
  `owner:phase:reason`). The budget also caps the actual lock wait. The
  holder can be in any container; see
  [Which holder admission weighs](#which-holder-admission-weighs). Only an
  `ollama` owner's model is sized for the fit check: a render owner's label
  (an image model, an audio engine) has no Ollama arch to read, so its fit
  gate is skipped without asking Ollama.
- `priority` — in-process wake order: `pipeline` > `operator` >
  `background`, FIFO within a class; a parked waiter is promoted one class
  per `gpu_sched_aging_seconds` waited, so background work can be delayed
  but never starved.

### Which holder admission weighs

The ETA gate needs a holder to estimate against, and the pipeline's GPU work
is split across containers. Content flows run in `poindexter-prefect-worker`.
Media renders (`video/media_render`, p90 ≈ 2530 s on prod) run in
`poindexter-worker`. Until 2026-09-25 admission read only its own process
(`_current_owner`), so a budgeted caller in prefect-worker saw no holder
behind a render in the other container. It was granted, spent its whole
budget at the pg-advisory step, and ended in `GpuLockTimeoutError` plus a
warn `gpu_lock_timeout` finding instead of an up-front `GpuBusyError`.

The holder is now resolved in this order:

1. **This process's own session**, when it holds a card the caller is about
   to take (the caller's `resolve_lock_keys`). This costs nothing beyond the
   stats read admission always did.
2. **Otherwise, Postgres.** `list_pg_holders()` lists every session holding
   the base key. `pg_holder_blocks` keeps only the sessions that would
   actually block this caller, using the per-key lock modes the query
   returns (`exclusive_keys`):

   | Caller                                                    | Blocked by                                                                                                  |
   | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
   | unscoped (`[7777777777]`, takes the base key exclusively) | any holder of the base key, in either mode                                                                  |
   | scoped (base key shared + its device keys exclusive)      | a holder of the base key in exclusive mode (an unscoped process), or a holder of one of its own device keys |

   So a GPU-1 judge is never refused because of a GPU-0 render: the render
   holds the base key shared and only GPU 0's device key. A scoped _waiter_
   holds the base key shared while it queues for its device key, so it shows
   up in `list_pg_holders`, but it is not mistaken for the holder. The caller
   has to outlast every blocker, so admission weighs the one with the longest
   estimated remaining time (`p90 − held_for_s`). All their stats come back in
   one `read_stats_many` query.

**Cost.** One `pg_locks` query, plus one stats query when something blocks.
It runs on the budgeted path only (`max_wait_s` set and `gpu_sched_enabled`
on), and only when no in-process holder answered.

**Fail-open, with one deliberate difference from the in-process path.** A
failed or empty lookup means no holder: the ETA gate is skipped and the caller
waits at the lock, bounded by its budget, as before. A cross-process blocker
with **no `gpu_lease_stats` profile** is also treated as no holder, rather
than given `gpu_sched_eta_fallback_seconds`. In this process a holder is
always a scheduler session, so "no stats" just means a phase too new to have
a p90. In Postgres it can be anything that takes the key. The brain's probes
(`brain_probe/content_gen`, `brain_probe/ollama_embedding`) hold it for
seconds and are never profiled, and the 120 s fallback would refuse a 45 s QA
rail behind a five-second probe. Untagged sessions (no `poindexter-gpu:` tag)
are skipped the same way.

The `gpu_admission_rejected` finding names the holder it weighed. `extra`
carries `holder_owner`, `holder_phase`, `holder_source` (`in_process` or
`postgres`) and `holder_elapsed_s`, and the body reads e.g.
`behind video/media_render (another process, held 300s)`. To see it working
in prod, look for rejects with `extra->>'holder_source' = 'postgres'` during a
media render. `gpu_lock_timeout` rows with `timeout_s` 45 or 120 from
prefect-worker callers should fall as they appear.

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

| Group               | Callers                                                                                                                                                    | Budget key (default)                         | What a refusal costs                                                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1 — QA rails        | `ragas_eval`, `deepeval_rails`                                                                                                                             | `gpu_sched_qa_rail_max_wait_s` (45s)         | Sentinel scores + a `qa_rail_gpu_busy_skip` finding. Never blocks publish, never fabricates a pass. |
| 2 — media stages    | `generate_media_scripts` (its LLM calls, and its two Stable Audio renders under `gpu.lock("video")`), `generate_video_shot_list`, `review_video_shot_list` | `gpu_sched_media_max_wait_s` (120s)          | The post ships without that artefact + a `media_gpu_busy_skip` finding.                             |
| 3 — operator images | `services/image_service.py` (`poindexter tasks regen-image` / `add-image`, `POST /api/tasks/{id}/generate-image`)                                          | `gpu_sched_operator_image_max_wait_s` (150s) | **Nothing is skipped** — a human gets an immediate 503 naming the holder ETA, and retries.          |

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

| Key                                   | Default  | Meaning                                                                                                              |
| ------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| `gpu_sched_enabled`                   | `false`  | Master switch for admission + wait-cap.                                                                              |
| `gpu_sched_eta_fallback_seconds`      | `120`    | Assumed holder ETA when a key has no stats yet.                                                                      |
| `gpu_sched_aging_seconds`             | `300`    | Priority-class promotion window (0 = no aging).                                                                      |
| `gpu_sched_qa_rail_max_wait_s`        | `45`     | Wait budget for the fail-soft QA rails (P2 group 1). `0` = unbounded.                                                |
| `gpu_sched_media_max_wait_s`          | `120`    | Wait budget for the media stages (P2 group 2). `0` = unbounded.                                                      |
| `gpu_sched_operator_image_max_wait_s` | `150`    | Wait budget for operator single-image renders (P2 group 3). `0` = unbounded.                                         |
| `gpu0_headroom_gb`                    | `6`      | VRAM held back for mid-hold invisible claims (desktop transients + idle-unloaded residents).                         |
| `gpu_evictable_process_pattern`       | `ollama` | Substring matching the primary Ollama runner in the per-process VRAM series.                                         |
| `gpu_queue_mirror_delay_seconds`      | `2`      | How long a wait must last before it appears in `gpu_queue`. The grace is what keeps an uncontended acquire zero-I/O. |

Pre-existing lock tunables (`gpu_lock_acquire_timeout_seconds`,
`gpu_lock_release_timeout_seconds`, `gpu_serialize_llm_dispatch`, the
gaming-detection knobs) are unchanged.

## Per-task GPU economics (`gpu_task_sessions`)

Every `gpu.lock(...)` that carries a `task_id` writes one `gpu_task_sessions`
row when it releases: phase, model, hold duration, and what the hold cost on
the GPU. The write is best-effort. Both locks are already released when it
runs, and a failure emits the info `gpu_task_session_write_failed` finding
instead of reaching the caller.

**Which cards.** `resolve_session_devices(owner, model)` follows the same
claim the lock follows, so a session is costed on the cards it held:

| Device scoping                           | Session                                     | Cards sampled                                               |
| ---------------------------------------- | ------------------------------------------- | ----------------------------------------------------------- |
| off, or `gpu_lock_scopes` will not parse | any                                         | `pipeline_gpu_index` (the pre-scoping behaviour)            |
| on                                       | role found in `gpu_lock_scopes`             | that role's cards (the qwen3-vl judge → `qa_judge` → GPU 1) |
| on                                       | unknown owner, or role missing from the map | every card in the map, which is what the lock takes         |
| on                                       | role mapped to `[]`                         | none: the row carries no GPU figures                        |

**What the row records.**

| Column                                         | Source                                                                                                                                                     |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gpu_model`                                    | the exporter's `nvidia_gpu_info{gpu,uuid,name}` name label (nvidia-smi's product name); two cards read `NVIDIA GeForce RTX 5090 + NVIDIA GeForce RTX 3090` |
| `avg_power_watts`                              | `avg_over_time(nvidia_gpu_power_draw_watts[<hold>s])` per card, summed across cards                                                                        |
| `peak_power_watts`                             | `max_over_time(...)` per card, summed (exact for one card; an upper bound across several, since cards need not peak together)                              |
| `avg_utilization_pct`                          | `avg_over_time(nvidia_gpu_utilization_percent[<hold>s])`, mean across cards                                                                                |
| `kwh_consumed`                                 | average power × hold                                                                                                                                       |
| `electricity_rate_kwh`, `electricity_cost_usd` | `app_settings.electricity_rate_kwh`, which `UpdateUtilityRatesJob` keeps current and the cost ledger reads, × kWh                                          |

A hold shorter than the 30 s scrape interval can contain no sample, so each
query falls back to the latest sample (PromQL `or`). That is the value the old
release-time read returned. A card with no reading leaves its figure NULL
rather than a partial sum, and an exporter image that predates
`nvidia_gpu_info` leaves `gpu_model` NULL with one WARNING per process. These
are card figures only. For whole-box energy, use the Shelly wall meter on the
Hardware & Power board.

**Rows written before 2026-09-25 are not comparable.** They carry the literal
`RTX 5090` for every session, GPU 0's draw read once at release, and a price
of `0.12` (the code default for `electricity_rate_kwh_usd`, a key nothing
seeds). Measured on the operator box that day, the release-time read was off
by up to ~9×: a 51-minute render averaged 307.7 W and was recorded at 36.8 W,
and 34 qwen3-vl `caption_image` sessions that ran on the RTX 3090 were
recorded as 5090 work. New rows name the card by its full nvidia-smi name, so
the old literal separates the two populations:

```sql
SELECT phase, gpu_model, count(*),
       round(sum(kwh_consumed), 3)         AS kwh,
       round(sum(electricity_cost_usd), 4) AS usd
  FROM gpu_task_sessions
 WHERE started_at > now() - interval '7 days'
   AND gpu_model IS DISTINCT FROM 'RTX 5090'
 GROUP BY 1, 2
 ORDER BY kwh DESC NULLS LAST;
```

## Per-process VRAM metric

The eviction credit needs `nvidia_gpu_process_memory_mib{gpu,pid,process}`
from the nvidia-smi exporter (`scripts/nvidia-smi-exporter.py`, container
`poindexter-gpu-exporter`), and the economics row above needs its
`nvidia_gpu_info` series. The exporter is baked into its image. When
`scripts/nvidia-smi-exporter.py` or `scripts/Dockerfile.gpu-exporter` changes,
`deploy-checkout-sync` rebuilds and force-recreates it. To do it by hand, run
from the deploy checkout (`start-stack.sh` supplies the bootstrap secrets the
compose file needs):

```bash
bash scripts/start-stack.sh build gpu-exporter
```

```bash
bash scripts/start-stack.sh up -d --no-deps --force-recreate gpu-exporter
```

`up -d` alone keeps the old container after a same-tag rebuild. Until the
rebuilt exporter serves the metric, the credit reads 0.0 and admission simply
never grants on eviction — conservative, not broken.

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
- `/api/gpu/queue` matches reality during a busy render window — check it
  from the CONSOLE's process while the holder is in another container, since
  that cross-process case is the one that used to read as an empty lock.
- Zero unexpected `gpu_admission_rejected` findings (there should be none
  at all until a caller passes a budget).
