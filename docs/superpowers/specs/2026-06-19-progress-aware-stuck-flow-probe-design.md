# Progress-aware Prefect stuck-flow probe — design

**Issue:** _to file — `Glad-Labs/poindexter` (OSS; the probe ships in the public mirror)_
**Date:** 2026-06-19
**Status:** Approved (progress-heartbeat approach)
**Surfaced by:** recurring false-alarm pages from `brain.prefect_stuck_flow_probe`
during a legitimately long content run (operator report 2026-06-19 ~00:48 local).

## Problem

`brain/prefect_stuck_flow_probe.py` pages the operator with
`Prefect queue backlog: N overdue scheduled run(s)` while a **healthy**
`content_generation` run is in flight. Confirmed live on 2026-06-19:

- One `RUNNING` flow (`amber-goldfish`) held the single `content-pool` slot
  for ~30 min, sitting on a late media node (`generate_media_scripts`,
  node 30 of 37) for the task "How GPU VRAM bandwidth shapes local LLM
  inference speed" (`pipeline_tasks.task_id=3cd7bea6…`).
- The deployment runs **`concurrency=1`** (the live work-pool limit — note
  `app_settings.prefect_content_flow_concurrency=3` is drift; the deploy
  script was never re-run, so prod is still 1). Every 2-minute cron tick
  therefore queued a flow run that could not dispatch — **44 `SCHEDULED`
  runs piled up**, 11 overdue by ≥ 5 min.
- The backlog detector counts those 11, sees `11 > 3`, and pages. It did so
  **three times in 13 minutes** (6 → 8 → 11 overdue) for this one run.

### Root cause

The backlog detector _infers_ "a stuck run is holding the slot" from "runs
are piling up." But a **legitimately long run** holding `concurrency=1`
produces an **identical** pile-up. To tell the two apart you must know whether
the slot-holder is _making progress_ — and **no durable per-node progress
signal exists today**:

- `atom_runs` skips the `stage.*` / media nodes (0 rows in 30 min despite an
  active run), so it is not a whole-graph heartbeat.
- `pipeline_tasks.updated_at` is coarse — bumped only by claim / certain
  stage DB-writes / finalize (observed 12.8 min stale mid-run).
- The `⚙️` Discord / Telegram per-node progress is **ephemeral** — it fans
  out via `template_runner._emit_progress` → `notify_operator` and in-place
  Telegram edits, and is never written to the DB
  (`pipeline_streaming.py` even reserves its `pool` param "for future
  channels that may persist progress").

Raising the threshold (the operator's first instinct) only moves the
false-positive line: the pile-up scales with run length, so a long-enough
run always re-crosses any fixed threshold, and the backlog detector becomes
redundant with the duration detector anyway.

### Second bug behind the same root cause

The `RUNNING`-stuck detector uses a **flat 30-minute** threshold and
`prefect_stuck_flow_auto_crash=true`. A media-heavy run that legitimately
exceeds 30 min is **force-CRASHED**, discarding ~30 min of work and bouncing
the task back to `pending`. Same blindness: duration ≠ stuck.

## Goals

1. Stop the backlog page firing while the slot-holder is progressing.
2. Stop the auto-crash killing healthy-but-slow runs.
3. Catch genuinely wedged runs _faster_ than the flat 30-min threshold.
4. Keep every tunable in `app_settings`; no regression if the new signal is
   absent.

## Non-goals (YAGNI)

- Intra-node heartbeating (e.g. image/video step callbacks). Node-granularity
  is enough; revisit only if a single node legitimately exceeds the stall
  window.
- `concurrency > 1` holder→task mapping. Documented as future work; the exact
  `concurrency=1` case is what bites today.
- Touching `reclaim_stale_inprogress_tasks` (the 30-min stale-sweep). It has
  the same "don't reap healthy long runs" blind spot and _could_ later adopt
  the new column — out of scope here.
- Fixing the `prefect_content_flow_concurrency` 3-vs-1 drift. Separate
  operational decision (raising it has VRAM implications); noted, not changed.

## Design

One durable signal — **`pipeline_tasks.last_progress_at`** — stamped by the
pipeline on every node start, read by the brain to distinguish _progressing_
from _wedged_. Two components.

### Component 1 — the heartbeat (pipeline side)

- **Schema:** new column `pipeline_tasks.last_progress_at timestamptz NULL`
  (migration; nullable, no backfill, no index — `in_progress` rows are
  `≈ concurrency`, so the probe's status-filtered read is already selective).
  Dedicated column rather than reusing `updated_at`, so existing
  `updated_at` consumers (notably the stale-sweep) keep their current
  semantics.
- **Stamp at claim:** `services/flows/content_generation.py::claim_pending_task`
  already runs `UPDATE … SET status='in_progress', updated_at=NOW()` — add
  `last_progress_at=NOW()` so the column is non-NULL from the first moment a
  task is claimed.
- **Bump per node:** a swallow-safe helper

  ```python
  async def record_node_progress(services: dict, task_id) -> None:
      # UPDATE pipeline_tasks SET last_progress_at = now() WHERE task_id = $1
      # fire-and-forget; never raises into the run (mirrors _safe_on_event)
  ```

  called from the `node_started` path in **both** node wrappers used by the
  `graph_def` path — `template_runner.make_stage_node` and
  `pipeline_architect._wrap_atom`. Both already resolve
  `database_service` via `_services_from_config(config)` (the `__services__`
  channel) and have `task_id` in `state`, so each call site is a one-line
  addition. A failed write leaves `last_progress_at` unchanged and never
  perturbs the pipeline.

  `node_started` (not `node_completed`) is the chosen trigger: it marks "a new
  node just began," which is the progress event we care about. Bumping on both
  was considered and dropped (doubles writes; doesn't help the "currently
  inside one long node" case, which the stall window must cover regardless).

`dev_diary` (5 fast nodes, no `graph_def`) does not need the heartbeat — it
never approaches the probe thresholds — but it inherits the claim-time stamp
for free.

### Component 2 — the probe (brain side)

New setting **`prefect_stuck_flow_progress_stall_minutes`** (default **20** —
above the ~13-min single-node gap observed live; tunable _down_ as confidence
builds, mirroring the existing thresholds' "tune downward" philosophy).
Seeded in `services/settings_defaults.py`.

Per cycle the probe reads the in-progress task progress once:

```sql
SELECT task_id, last_progress_at,
       EXTRACT(EPOCH FROM (now() - last_progress_at))/60.0 AS mins_since_progress
FROM pipeline_tasks
WHERE status = 'in_progress'
ORDER BY last_progress_at DESC NULLS LAST;
```

With `concurrency=1` there is at most one such row, so "is _the_ in-progress
task progressing?" answers "is the slot-holder healthy?" exactly — no
Prefect-run → task ID stitching required.

- **Backlog page — gated.** Before paging on `overdue_scheduled_count >
queue_depth_threshold`, check for a _progressing holder_: an `in_progress`
  task whose `last_progress_at` is within `progress_stall_minutes`.
  - progressing holder present → **suppress** (the backlog is the expected
    shadow of a legit long run on `concurrency=1`; it drains itself).
  - no holder, or holder stalled → **fire** (a genuinely held/dead slot).
- **RUNNING auto-crash — redefined.** "Stuck" changes from "RUNNING >
  `prefect_stuck_flow_threshold_minutes`" to "RUNNING **and** the holder's
  `last_progress_at` is older than `progress_stall_minutes`." A run that
  reached node 30 and started a node 3 min ago is not crashed, even at 35 min.
  The page + `set_state` CRASHED mechanics are unchanged — only the _trigger_
  is progress-aware.
- **PENDING — unchanged.** PENDING means the run never started (no node, no
  progress signal); the flat `prefect_stuck_flow_pending_threshold_minutes`
  (5) stays correct.

### Backcompat (shim rule)

When `last_progress_at IS NULL` — pre-migration `in_progress` rows, or a
heartbeat write that failed — the probe **falls back to the existing flat
`prefect_stuck_flow_threshold_minutes` (30)** on the run's RUNNING age, and
the backlog gate treats "NULL holder" as "cannot confirm progress" → it does
**not** suppress (preserving today's fire-on-backlog behaviour). So the worst
case is exactly current behaviour; the new column can never make detection
worse, only better.

### Data flow

```
claim_pending_task ──set last_progress_at = now()──┐
                                                    ▼
every node_started ──record_node_progress()──► pipeline_tasks.last_progress_at
                                                    ▼
brain probe (5-min cycle) ──reads in_progress progress──►
        progressing within stall  → suppress backlog / skip auto-crash
        stalled or NULL (legacy)   → page + (auto-)crash per existing rules
```

## Testing (contract tests + docs, per house defaults)

**Pipeline (unit):**

- `claim_pending_task` stamps `last_progress_at` on claim.
- `record_node_progress` issues the UPDATE for a given `task_id`; swallows a
  raising/closed pool without propagating.
- A `graph_def` node invocation calls `record_node_progress` once on
  `node_started` (one assertion each for the stage-node and atom-node
  wrappers).

**Probe (unit, inject `pool` + `notify_fn` spies):**

- Backlog **suppressed** when an `in_progress` task progressed within the
  window (the live `amber-goldfish` case).
- Backlog **fires** when no `in_progress` task exists, or its
  `last_progress_at` is stale.
- RUNNING auto-crash **fires** when the holder is stalled > stall_minutes;
  **does not fire** when the holder is progressing (even past 30 min).
- `last_progress_at IS NULL` → **legacy flat-threshold path** (backcompat:
  same decisions as today).

**Docs:**

- `brain/prefect_stuck_flow_probe.py` module docstring (new progress logic).
- The `prefect_stuck_flow_probe` description in `CLAUDE.md`.
- `docs/reference/app-settings.md` — new
  `prefect_stuck_flow_progress_stall_minutes` key.

## Rollout

- Migration adds the nullable column; idempotent on prod (column add only).
- `settings_defaults.py` seeds the new key on next boot.
- Heartbeat writes begin on the next claimed task; existing `in_progress`
  rows (NULL column) ride the legacy path until they finish.
- After the worker image / bind-mount picks up the pipeline change, restart
  the prefect-worker so new runs stamp progress.

## Open questions

None. Two operator-facing defaults were chosen with rationale (dedicated
column over reusing `updated_at`; stall default 20 min) and are tunable /
reversible at review.
