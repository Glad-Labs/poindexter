# Console Live Activity — "What is the system doing right now?"

**Date:** 2026-07-10
**Status:** Design approved, pending spec review → implementation plan
**Related:** PR #2260 (the mock NOW RUNNING band) — merged to main; Phase 1 evolves it live

## Problem

When Matt opens the operator console, he cannot tell what the system is doing
_at that moment_. To find out, he has to open Loki and read unstructured logs
and correlate by hand — and even then it's unclear exactly what is running.

The root cause is **not** a UI gap — it's that **no unified "what's running
now" signal exists** in the system. Live activity is spread across two
schedulers that share no source of truth:

- **Content pipeline** runs on **Prefect** (`@flow`/`@task`,
  `services/flows/content_generation.py`). Partly observable via
  `pipeline_tasks.status` and the current graph node, but even that is
  after-the-fact.
- **The ~43 background jobs** run on a **separate in-worker APScheduler**
  (`plugins/scheduler.py`). The scheduler records only each job's
  `last_run_at` / `last_status` in `job_run_state` — there is **no
  "currently running" record anywhere**. While a job executes, the _only_
  trace is the logs.
- **Media renders** are the sharpest case: they run _inline inside_ one of
  those background jobs (`dispatch_media_pipeline` → `_run_media_pipeline`,
  one-at-a-time, `idempotent=False`), so "is media rendering right now?"
  literally equals "is that job executing right now?" — which nothing records.
  There is no per-shot progress table; the mock band's "shot 6/9 · 67%"
  granularity does not exist in the backend today.
- **Brain daemon** is a separate process with its own tables + logs.

So the console can show a little content-task state and nothing else. Logs are
the only cross-cutting view, which is exactly why they're the fallback and why
they're unclear.

## Goal & success criteria

A **whole-system pulse**: at a glance, know what every moving part is doing
right now, without opening logs.

- **Scope:** the whole system's pulse — content tasks **prominent**, plus
  background jobs, media renders, and brain cycles **always visible**, even when
  the content pipeline is idle (proof the autonomous system is alive and
  working).
- **Depth:**
  - **Jobs → liveness** — presence + subject + elapsed ("is it alive / on-task /
    wedged?").
  - **Content & media → real progress** — presence + how-far-along.
  - **Recent trail** — a short "what just happened" list (last N finished/failed
    with durations), so a glance also covers "what did it just do."
- **Freshness:** near-real-time (~3 s poll while active, backing off when idle).
- **Success:** Matt never opens Loki to answer "what's happening / did it just
  do X / is it stuck?" again.

## Non-goals (YAGNI)

- Not a log replacement or per-line trace — Loki (deep logs) and Langfuse (LLM
  waterfalls) remain the drill-down surfaces; the pulse links out to them.
- Not a Grafana replacement — historical time-series stays in Grafana (the
  ledger can _feed_ Grafana later, but that's not this epic).
- No new alerting in this epic — the ledger is the foundation a future
  "stuck job" alert can build on, but we don't build the alert here.
- Media progress granularity is only as fine as each render actually exposes
  (per-shot for video, stage-level for podcast) — we never fabricate a %.

## Architecture — the activity ledger as the seam

One durable source of truth for "what's happening," decoupled from _who_ is
doing it.

```
  producers (rented)                 the seam                 consumers
  ─────────────────                  ────────                 ─────────
  APScheduler jobs  ─┐
  template_runner   ─┼─▶  live_activity  ─▶  GET /api/activity ─▶ CONSOLE band
  media atoms       ─┤     (ledger table)                       ─▶ persistent strip
  brain cycle       ─┘                                          ─▶ WALL pulse
  (later: Prefect)  ─┘
```

The **ledger is the interface; producers are rented implementations.** Today
APScheduler writes the `job` rows; when we do the **C follow-up** (consolidate
schedulers onto Prefect), Prefect writes the _same_ rows — the console contract
never moves. That is what makes A→C a **producer swap, not a rewrite.**

**Why a new table, not an existing one** (checked): `job_run_state` is
last-run-per-job (kept, for schedule anchoring); `atom_runs` is post-hoc
per-atom analytics; `audit_log` is append-only point-in-time events.
`live_activity` is the only one with **mutable in-flight rows carrying live
progress + heartbeat across every activity kind**. Overloading any of the three
distorts its purpose.

## Data model — `live_activity`

| column         | type               | notes                                         |
| -------------- | ------------------ | --------------------------------------------- |
| `id`           | `BIGSERIAL`        | pk                                            |
| `kind`         | `TEXT`             | `job` · `content` · `media` · `brain`         |
| `ref_id`       | `TEXT`             | task_id / job_name / post id                  |
| `title`        | `TEXT`             | human label                                   |
| `status`       | `TEXT`             | `running` · `ok` · `fail` · `stale`           |
| `step`         | `TEXT` null        | current-step label (content/media)            |
| `progress_pct` | `SMALLINT` null    | 0–100 (content/media only)                    |
| `detail`       | `JSONB`            | kind-specific extras (prefect_run_id, model…) |
| `started_at`   | `TIMESTAMPTZ`      | default `now()`                               |
| `updated_at`   | `TIMESTAMPTZ`      | heartbeat, default `now()`                    |
| `finished_at`  | `TIMESTAMPTZ` null | NULL ⇒ live                                   |

Indexes:

- partial `(started_at DESC) WHERE finished_at IS NULL` — the "running" read.
- partial `(finished_at DESC) WHERE finished_at IS NOT NULL` — the "recent" read.

**Table DDL** ships as a fresh `YYYYMMDD_HHMMSS_*.py` migration (schema only, per
the migrations convention). **Retention** via the declarative `retention_policies`
plane (keep finished rows ~24–48 h); the seed row follows the seeds-not-in-
migrations convention (baseline.seeds.sql / idempotent boot seed — exact seam
resolved in the plan, since the table is post-baseline).

Honesty of `progress_pct`:

- **content** = graph **node position** (`node_index / total_nodes`), not
  time-elapsed. Documented as such in the UI tooltip.
- **media** = whatever the render exposes — per-shot for video, stage-level for
  podcast. Never a fabricated number.

## Producers

**One small service — `services/live_activity.py`:**
`begin(kind, ref_id, title, *, detail=…) -> id`, `update(id, *, step=…, pct=…)`,
`finish(id, status)`. Producers call these; **no raw SQL at call sites** (keeps
the service-layer contract). **Every write is best-effort** — wrapped so a
ledger failure logs and continues, never breaking the real job/pipeline. This is
observability; it must never become load-bearing.

**Phase 1 — the whole pulse, from chokepoints:**

- **Jobs (liveness)** — `PluginScheduler._runner()` (`scheduler.py:183`):
  `begin()` before `await job.run(...)`, `finish()` in a `finally`. **One edit
  captures all ~43 jobs + `dispatch_media_pipeline`.** Elapsed derived at read.
- **Content (progress)** — `template_runner`'s node loop: `begin()` when a task
  starts, `update(step=node, pct=node_index/total)` per node, `finish()` on
  completion/halt. Cheap — the loop already runs every node.
- **Brain (liveness)** — one `begin/finish` around the brain daemon's monitor
  cycle (it already speaks asyncpg to the DB — the spinal cord).

Phase 1 alone gives the full picture, and media already shows up as
**"`dispatch_media_pipeline` · running 3m"** (job-level liveness) — honest and
already better than digging logs.

**Phase 2 — media depth (the instrumentation):**

- **Media (progress)** — the `media_pipeline` render atoms emit
  `update(step="shot 6/9", pct=…)` as shots render / audio mixes. Video gets
  per-shot; podcast gets stage-level.

**Phase 3 = the C follow-up** — jobs migrate onto Prefect, which writes the same
`job` rows (or a thin Prefect→ledger bridge). Console untouched.

## Read API

- **`GET /api/activity`** — thin route → service fn `get_live_activity()`
  (OAuth-JWT, like every other console read). Two indexed queries.

```json
{
  "running": [
    {
      "kind": "content",
      "ref_id": "4412",
      "title": "pgvector at Small Scale",
      "status": "running",
      "started_at": "…",
      "step": "qa.critic",
      "progress_pct": 62
    },
    {
      "kind": "job",
      "ref_id": "dispatch_media_pipeline",
      "title": "Media dispatch",
      "status": "running",
      "started_at": "…"
    }
  ],
  "recent": [
    {
      "kind": "job",
      "title": "topic-harvest",
      "status": "ok",
      "started_at": "…",
      "finished_at": "…",
      "duration_ms": 8200
    }
  ],
  "summary": {
    "running_by_kind": { "content": 1, "job": 1, "media": 0, "brain": 0 }
  }
}
```

- `running` = `finished_at IS NULL` **and** `updated_at` within the freshness
  window (see edge cases), ordered content/media first then jobs by `started_at`.
- `recent` = last ~20 finished, `finished_at DESC`, each with its own duration.
- Console adds `PX.api.activity()` on the existing `usePolledResource` pattern —
  **~3 s poll**, backing off to ~10 s when nothing is running.

## Console surfaces (one source, three views)

1. **CONSOLE tab — evolved band** (mockup ①; `js/nowrunning.jsx`, from PR #2260).
   Three columns: **In Production** (content + media with progress bars),
   **Background** (running jobs + brain, liveness + elapsed), **Just Happened**
   (recent trail with durations). Top of the overview.
2. **Persistent strip** (mockup ③) — a slim always-on bar rendered in the
   rail/header, visible in **every** mode: `content 62% · media 55% · 2 jobs ·
brain ✓`, click-to-expand into a drawer with the full running list + trail.
3. **WALL tab — enhance `WallDisplay`** (mockup ②; `js/modes.jsx:455`). The WALL is already
   an "ambient always-on HUD" (status + clock + attention + stat grid); the live
   pulse becomes its centerpiece — big progress cards for what's in production, a
   live background-jobs list, and the full trail — while status/clock/attention
   stay. A leave-it-open command center.

All three consume the one `PX.api.activity()` read; all three render in mock mode
from a `PX.activity` seed (zero-backend, like the rest of the console).

## States & edge cases (the console's honest-state discipline)

- **Idle** → "nothing in flight," but `recent` still shows what just ran. Idle is
  informative, never blank.
- **Read fails** → retain last-good + mark stale (`usePolledResource` `fresh`
  pattern), so a blip never reads as "system idle."
- **Orphaned `running` rows** (worker died mid-job) — the real trap. Two guards:
  (1) the read only counts a row "running" if `updated_at` is within a freshness
  window; (2) a tiny reaper marks long-stale rows `stale`/`fail` (mirrors the
  existing stale-inprogress task reclaim). No "running forever" ghosts.
- **Ledger write fails** → swallowed, logged, never breaks the producer. The row
  just doesn't appear — an honest gap, not a crash.
- **Mock mode** → `PX.activity` seed so all three surfaces render with no backend.

## Testing (matches existing conventions)

- `services/live_activity.py` — unit tests incl. best-effort swallow-on-error.
- Producers — scheduler wrapper emits `begin`+`finish` around a job run;
  `template_runner` emits progress; media atoms emit progress (Phase 2).
- `GET /api/activity` — contract test (the console `__tests__/contracts` pattern).
- Console — pulse mappers (elapsed / progress / trail derivation) as pure JS →
  `node --test` (the console's logic-module test convention).
- Reaper — an orphaned `running` row past the window → marked `stale`.

## Phasing

- **Phase 1** — ledger + `services/live_activity.py` + producers (jobs, content,
  brain) + `GET /api/activity` + **CONSOLE band**. The pulse ships; media is
  job-level liveness.
- **Phase 1.5** — **persistent strip** (all modes). Cheap; same data.
- **Phase 2** — **WALL pulse** enhancement + **media render progress**
  instrumentation.
- **Phase 3** — **C consolidation**: migrate APScheduler jobs onto Prefect; it
  writes the same `job` rows. Console unchanged.

## Relationship to PR #2260

PR #2260 (the mock NOW RUNNING band — content-tasks + media + vitals) is **merged
to main**. It is the scaffold for the CONSOLE band (surface ①); Phase 1 reframes
its columns (In Production / Background / Just Happened) and wires them to
`/api/activity`, so the band ships live rather than mock. (The taskStatusKind
over-mapping bug found while investigating — 21/50 recent terminal tasks reading
as "run" — is fixed as part of the content producer / read, which sources
truly-running work from the ledger, not the loose status filter.)

## Open decisions (resolve in the plan)

- Exact retention seam for the `retention_policies` row on a post-baseline table
  (baseline.seeds.sql ordering vs. idempotent boot seed).
- Persistent-strip placement (top header vs. bottom bar) and expand behavior
  (inline drawer vs. deep-link to WALL).
- Freshness-window + reaper thresholds (app_settings, tunable).
- Media progress granularity per render type (what the atoms can actually emit).
