# Design: SEO Harvest Loop — Milestone B (auto-enqueue + outcome measurement)

- **Date:** 2026-06-12
- **Status:** Approved design — ready for implementation plan
- **Author:** Matt + Claude (brainstorming session)
- **Scope:** Phase 2b (auto-enqueue) + Phase 2c (outcome tracking) of the SEO Harvest Loop
- **Parent design:** [`2026-06-11-seo-harvest-loop-design.md`](2026-06-11-seo-harvest-loop-design.md) (§3 Phase 2b/2c)
- **Tracks:** Glad-Labs/poindexter#763 (epic #762)

---

## 1. Context

Milestone A shipped the `seo_refresh` graph_def (4 nodes:
`content.load_existing_post` → `seo.optimize_metadata` →
`atoms.approval_gate` → `content.republish_post`) and was **validated live on a
real production post** — a hand-enqueued refresh ran to the gate, was approved,
and the live title/meta updated (R2 export + ISR). Three optimizer/loop defects
found during validation are all fixed and merged (#1456 ×2, #1461 ×1).

Today nothing **auto-enqueues** a refresh, and nothing **measures** whether a
refresh actually moved the needle. Milestone B closes both: it auto-enqueues
refreshes from the analyzer's ranked opportunity list (gated, capped, still
sign-off-first), and it measures GSC position/CTR delta N days after each
refresh — the empirical proof the loop works.

The overarching architecture is already approved in the parent design doc; this
spec covers only the remaining Phase 2 mechanics and the two prerequisite fixes
that the live validation surfaced.

### Scope decisions (this milestone)

- **Excludes #764** (GSC query-dimension ingestion + `GscQueryGapSource`). The
  optimizer is query-optional and works on existing page-level data. #764 stays a
  separate future workstream. _Rationale: keeps Milestone B tight and provable;
  #764 multiplies `external_metrics` row volume and needs its own retention work._
- **Excludes auto-publish graduation.** `seo_refresh_gate` stays **enabled** —
  every auto-enqueued refresh still pauses for operator sign-off. The
  edit-distance graduation (`seo.refresh.auto_publish_after_clean_runs`) is a
  later increment. _Rationale: "autonomy earned"; no refresh track record exists
  yet._

### Cost / quota note (resolved during brainstorming)

The loop's ranking data is **Google Search Console (GSC)**, not GA4
(`post_performance.google_impressions/clicks/avg_position` ←
`external_metrics WHERE source='google_search_console'`; GA4 only feeds
`avg_time_on_page_seconds`, unused for tiering). Both the GSC Search Analytics
API and the GA4 Data API are **free** (quota-limited, never dollar-billed).
**Milestone B adds zero new Google API calls** — both new jobs read the local
Postgres mirror (`seo_opportunities`, `post_performance`, `pipeline_tasks`); the
only thing that hits Google is the existing Singer-tap ingestion, unchanged
here. So the jobs are safe to run on any cadence. (The one piece that would
increase the Google fetch is #764 — explicitly deferred.)

---

## 2. Goals & non-goals

### Goals

1. **Auto-enqueue** `seo_refresh` tasks from the top open opportunities — gated
   on `seo.refresh.enabled` (default off), capped per run, deduped against
   in-flight tasks, and parking rejected refreshes so they don't re-fire.
2. **Measure outcomes** — N days after a refresh, re-read GSC position/CTR and
   record the delta vs the pre-refresh baseline.
3. **Fix the two loop prerequisites** the live validation exposed (status-clobber
   in the analyzer upsert; missing `refreshed_at` anchor).
4. **Observe it** — Grafana panels for the refresh queue and the outcome delta.
5. Everything tunable via `app_settings`; read-only measurement; no new Google
   calls.

### Non-goals (this milestone)

- #764 query-dimension ingestion / `GscQueryGapSource`.
- Auto-publish graduation (edit-distance trust → no-sign-off republish).
- Auto-retry of rejected refreshes (rejected opportunities park; operator resets
  by hand — see §6 known limitation).
- Re-opening `refreshed` opportunities for a second refresh based on a poor
  outcome (the outcome job records the delta; acting on it is future).

---

## 3. Architecture

Five units. Two are fixes to existing code (the dedup + measurement foundation);
two are new scheduled jobs; one is the Grafana panel set. All read/write only
local tables.

### 3.1 P1 — analyzer upsert preserves non-open status (fix)

**File:** `services/seo/striking_distance.py::_UPSERT_SQL`

The daily analyzer's `ON CONFLICT (post_id, target_query) DO UPDATE`
unconditionally sets `status = 'open'`, so any post we move to `queued` /
`refreshed` / `dismissed` reverts to `open` within 24h — which, once auto-enqueue
exists, re-refreshes it forever. Change the one clause to latch non-open statuses:

```sql
status = CASE
    WHEN seo_opportunities.status IN ('queued','refreshed','dismissed')
    THEN seo_opportunities.status
    ELSE 'open'
END
```

Metric columns (`current_position`, `impressions`, `ctr`, `gap_score`) keep
refreshing every run — only the status latch changes. `baseline_*` columns are
already untouched by the upsert (preserved). This is the foundation the whole
dedup depends on.

### 3.2 P2 — `refreshed_at` timestamp (fix)

**Files:** new migration (DDL) + `modules/content/atoms/content_republish_post.py`

Add `refreshed_at TIMESTAMPTZ` to `seo_opportunities` (nullable, no default). The
`content.republish_post` atom — which already stamps `status='refreshed'` +
`baseline_position`/`baseline_ctr` — also sets `refreshed_at = NOW()` in the same
`UPDATE`. This is the anchor the outcome job gates "measure N days after the
refresh" on; nothing else records when a refresh happened.

`_STAMP_OPP_SQL` becomes:

```sql
UPDATE seo_opportunities
   SET status            = 'refreshed',
       baseline_position = current_position,
       baseline_ctr      = ctr,
       refreshed_at      = NOW()
 WHERE id = $1::uuid
```

### 3.3 J1 — `enqueue_seo_refreshes` job (new)

**Files:** `services/jobs/enqueue_seo_refreshes.py` + `plugins/registry.py` row +
`plugin.job.enqueue_seo_refreshes` default config.

Gated on `seo.refresh.enabled` (default `false`) — short-circuits to a no-op
`JobResult(ok=True)` when off, mirroring the analyzer's `analyzer_enabled` guard.
Schedule: `every 6 hours` (tunable via `config.schedule`). Per run:

1. **Select** the top `seo.refresh.max_per_run` (new setting, default `3`)
   `status='open'` opportunities ordered by `gap_score DESC`. (All tiers eligible;
   gap_score already encodes priority.)
2. **Dedup** — for each candidate, skip if the post already has an **active**
   `seo_refresh` task in `pipeline_tasks`. "Active" is an explicit whitelist of
   in-flight states — `status IN ('pending','in_progress','awaiting_gate','awaiting_approval')`
   — chosen over blacklisting terminal states so a future status can't silently
   slip through as "active". (Why this guard matters even though enqueue parks
   rows at `queued`: a _manually_-enqueued refresh — like the validation runs —
   leaves the opportunity at `open`, so without this check auto-enqueue could
   create a second task for the same post.) The task↔post link is
   `pipeline_versions.stage_data->'task_metadata'->>'post_id'` (the seam
   `add_task` writes and `_load_task_metadata` reads).
3. **Enqueue** via `TasksDatabase.add_task({...})` with
   `task_type='seo_refresh'`, `template_slug='seo_refresh'`,
   `task_metadata={post_id, seo_opportunity_id, target_query}`, `status='pending'`.
4. **Park** — flip the opportunity `status='queued'` (so a later analyzer run
   won't re-open it, and a rejected refresh won't be immediately re-enqueued).
5. **Emit a finding** summarizing what was queued (post slugs + gap_scores).

Returns `JobResult(ok=True, changes_made=<n queued>, metrics={...})`. Per-row
try/except so one bad candidate never aborts the run.

### 3.4 J2 — `measure_seo_refresh_outcomes` job (new)

**Files:** `services/jobs/measure_seo_refresh_outcomes.py` + `plugins/registry.py`
row + `plugin.job.measure_seo_refresh_outcomes` default config.

Read-only / safe-on (no master switch — it only ever measures `refreshed` rows;
no refreshes → no-op). Schedule: `every 24 hours`. Per run:

1. **Select** opportunities where
   `status='refreshed' AND outcome_measured_at IS NULL AND refreshed_at IS NOT NULL
AND refreshed_at < NOW() - (seo.refresh.outcome_measure_after_days · interval '1 day')`.
2. **Re-read** the latest `post_performance` snapshot per post (the same
   `DISTINCT ON (post_id) ... ORDER BY measured_at DESC` source the analyzer
   reads — `google_avg_position`, `google_clicks`, `google_impressions`).
3. **Write** `outcome_position`, `outcome_ctr` (= clicks/impressions),
   `outcome_measured_at = NOW()`.
4. **Emit a finding** per measured post with the delta vs `baseline_position` /
   `baseline_ctr` (improved / flat / regressed).

Returns `JobResult(ok=True, changes_made=<n measured>, metrics={...})`. Per-row
try/except. No content mutation, no Google calls.

### 3.5 Grafana panels

**File:** `infrastructure/grafana/dashboards/seo-harvest.json` (repo JSON = SoT,
file-provisioned). Add to the existing SEO Harvest dashboard:

- A **refresh-queue stat row**: counts of `queued` and `refreshed` opportunities
  (and `open` for context).
- A **refresh-outcome table**: per refreshed post, `baseline_position` →
  `outcome_position` and `baseline_ctr` → `outcome_ctr` with computed deltas,
  filtered to `outcome_measured_at IS NOT NULL`. This is the panel that proves the
  loop works.

Validate with `python scripts/ci/grafana_panels_lint.py` (or the repo's panel
linter) before commit.

---

## 4. Data flow

```
[analyzer job, daily]          reads post_performance (GSC) → upserts seo_opportunities
   status latch (P1) keeps queued/refreshed/dismissed sticky
        │
        ▼
[enqueue job, 6h, gated]       picks top-N open by gap_score, dedups vs in-flight
   → add_task(seo_refresh)      pipeline_tasks row (pending)
   → opportunity.status='queued'
        │
        ▼
[seo_refresh graph]            load_post → optimize_meta → APPROVAL GATE → republish
   republish stamps:           status='refreshed', baseline_*, refreshed_at (P2)
        │
        ▼ (N days later)
[outcome job, daily, safe-on]  refreshed rows past the delay → re-read post_performance
   → outcome_position/ctr, outcome_measured_at, emit delta finding
        │
        ▼
[Grafana]                      queue counts + outcome-delta table
```

---

## 5. Data model & config changes

**Schema (one migration):** `ALTER TABLE seo_opportunities ADD COLUMN
refreshed_at TIMESTAMPTZ` (nullable). Light-import migration (stdlib only) for
migrations-smoke. No other schema change — `outcome_*` / `baseline_*` columns
already exist.

**Settings (`settings_defaults.py`, seeded every boot):**

| Key                                      | Default | Meaning                                      | Status  |
| ---------------------------------------- | ------- | -------------------------------------------- | ------- |
| `seo.refresh.max_per_run`                | `3`     | Cap on tasks the enqueue job creates per run | **new** |
| `seo.refresh.enabled`                    | `false` | Master switch for auto-enqueue               | exists  |
| `seo.refresh.outcome_measure_after_days` | `14`    | Delay before measuring                       | exists  |
| `pipeline_gate_seo_refresh_gate`         | `true`  | Sign-off gate stays on                       | exists  |

**Job config rows** (baseline-seed style, in `settings_defaults.py` per the
plugin-job convention): `plugin.job.enqueue_seo_refreshes`
(`{"enabled": true, "config": {"schedule": "every 6 hours"}}`) and
`plugin.job.measure_seo_refresh_outcomes`
(`{"enabled": true, "config": {"schedule": "every 24 hours"}}`). NOTE: the
**`enabled` here is the job-scheduler switch** (whether the job _fires_); the
content-mutating safety gate is `seo.refresh.enabled` read _inside_ J1. J1 fires
every 6h but no-ops until `seo.refresh.enabled=true`.

---

## 6. Error handling & known limitations

- Both jobs follow the analyzer's pattern: per-row `try/except` (one bad row
  never aborts), top-level failure → `JobResult(ok=False, detail=...)`
  (non-fatal, logged). No silent defaults — a missing required setting reads its
  seeded default; absent that, fail loud per `feedback_no_silent_defaults`.
- **The enqueue job only ever creates a _gated_ task.** It never publishes; the
  approval gate is the publish control. Worst case of a bug is an extra task that
  pauses for sign-off.
- **Known limitation — rejected refreshes park at `queued`.** If a refresh is
  rejected at the gate (or its task fails), the opportunity stays `queued` and is
  not auto-re-enqueued, even after an optimizer fix. This is deliberate for v1
  (operator-in-the-loop; a rejection is a signal to fix the optimizer, as #1456
  was). Operator resets to `open` by hand to retry. Auto-retry is a
  graduation-era concern. Documented, not silently swallowed.

---

## 7. Testing strategy

Per project convention (contract tests + doc updates every change):

- **P1 upsert** — unit test over synthetic `seo_opportunities`: re-running the
  upsert on a `queued` / `refreshed` / `dismissed` row preserves its status while
  refreshing metrics; an `open` / new row lands `open`.
- **P2 republish stamp** — extend `test_content_republish_post.py`: after `run()`,
  the opportunity row has `refreshed_at` set (alongside the existing
  status/baseline assertions).
- **J1 enqueue** — synthetic opportunities + `pipeline_tasks`: respects
  `max_per_run`; orders by `gap_score`; skips posts with a non-terminal
  `seo_refresh` task; flips enqueued rows to `queued`; no-ops when
  `seo.refresh.enabled=false`.
- **J2 outcome** — synthetic `refreshed` rows + `post_performance`: selects only
  rows past the delay with `outcome_measured_at IS NULL`; writes
  outcome_position/ctr correctly; computes the baseline delta; ignores rows
  inside the delay window.
- **Config tunability** — `max_per_run` / `outcome_measure_after_days` read from
  settings, not constants.
- **Migration** — `refreshed_at` add is idempotent (`ADD COLUMN IF NOT EXISTS`)
  and light-import (migrations-smoke).

---

## 8. Component boundaries

| Unit                   | Lives in                                                      | Reads                                   | Writes                                                    | Mutates content?                   |
| ---------------------- | ------------------------------------------------------------- | --------------------------------------- | --------------------------------------------------------- | ---------------------------------- |
| P1 upsert status latch | `services/seo/striking_distance.py`                           | —                                       | `seo_opportunities.status`                                | No                                 |
| P2 refreshed_at        | migration + `modules/content/atoms/content_republish_post.py` | —                                       | `seo_opportunities.refreshed_at`                          | No (atom already mutates the post) |
| J1 enqueue             | `services/jobs/enqueue_seo_refreshes.py`                      | `seo_opportunities`, `pipeline_tasks`   | `pipeline_tasks` (gated task), `seo_opportunities.status` | No (creates gated task)            |
| J2 outcome             | `services/jobs/measure_seo_refresh_outcomes.py`               | `seo_opportunities`, `post_performance` | `seo_opportunities.outcome_*`                             | No (read-only measure)             |
| Grafana panels         | `infrastructure/grafana/dashboards/seo-harvest.json`          | `seo_opportunities`                     | —                                                         | No                                 |

Each unit is testable in isolation against synthetic fixtures. The enqueue job is
the only content-adjacent unit and is guarded most carefully (it only creates a
gated task).

> **Placement note:** the analyzer + its `seo/` reader live in substrate
> (`services/seo/`) per the parent design (pure analytics over substrate tables).
> The new jobs are substrate scheduled jobs (`services/jobs/`, the established
> pattern) — they orchestrate over `seo_opportunities` + `pipeline_tasks` and do
> not import content-pipeline code. The content-mutating atom (P2) lives in
> `modules/content/atoms/` where it already is.

---

## 9. Decision log

- **Separate enqueue job, not bolted onto the analyzer** (Matt's call). Keeps the
  analyzer read-only (gated `analyzer_enabled`); enqueue mutates (gated
  `seo.refresh.enabled`) with its own schedule, cap, and observability.
- **Exclude #764** (Matt's call). Page-level data is enough for v1; query
  ingestion is a separate workstream with its own row-volume/retention work.
- **Defer auto-publish graduation** (Matt's call). Sign-off on every refresh;
  graduation after a track record exists.
- **Park rejected refreshes at `queued`** rather than auto-retry — operator-in-the-
  loop v1 default; prevents re-refreshing a just-rejected post.
- **Status latch over status-reset in the analyzer upsert** — the minimal fix that
  makes dedup correct without changing the analyzer's read-only character.
- **`refreshed_at` column over reusing `detected_at`/`outcome_measured_at`** —
  `detected_at` is bumped every analyzer run (not a refresh anchor);
  `outcome_measured_at` is the measurement time, not the refresh time. A dedicated
  column is unambiguous.
- **Outcome job is safe-on (no master switch)** — it only touches `refreshed`
  rows and makes no Google calls, so it's harmless when there's nothing to
  measure; one fewer knob.
