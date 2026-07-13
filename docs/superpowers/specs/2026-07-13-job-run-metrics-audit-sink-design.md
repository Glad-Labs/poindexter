# Generic `JobResult.metrics` → Grafana sink

**Date:** 2026-07-13
**Status:** Approved (design forks resolved with operator)
**Issue:** [Glad-Labs/poindexter#853](https://github.com/Glad-Labs/poindexter/issues/853)

## Problem

`plugins/scheduler.py::_runner` runs every registered `_SAMPLES` job and
consumes the returned `plugins.job.JobResult`, but it only:

- logs `ok` / `detail` / `changes_made`,
- bumps in-process counters (`_jobs_run/_succeeded/_failed`),
- escalates failures (`_escalate_job_failure`), and
- UPSERTs a last-run row into `job_run_state` (one row per job).

The `JobResult.metrics` dict is **discarded** — never persisted, never
emitted to Prometheus. So no scheduled job's custom metrics can reach a
Grafana panel today; they exist only as Loki log lines. **44 jobs already
populate `metrics`** (e.g. `crosspost_to_devto` returns
`posts_attempted` / `posts_crossposted` / `errors`), and every one of those
signals is currently invisible to the dashboards. This violates
`feedback_grafana_everything` for the entire job fleet.

## Decision

Write a **`job_run` row into `audit_log`** (the canonical historical record)
on each metrics-emitting fire, and document a **reusable Grafana panel
pattern** that reads those rows via the `local-brain-db` Postgres datasource
— the same seam the Findings and QA-Rails dashboards already use.

### Fork 1 — sink mechanism: `audit_log` (chosen)

Considered three options:

| Option                                        | Verdict    | Why                                                                                                                                                                                                                                               |
| --------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`audit_log` rows** (`event_type='job_run'`) | **Chosen** | No new table/migration. Exact precedent: Findings (`event_type='finding'`) and QA-Rails (`event_type='qa_pass_completed'`) already read `audit_log` via `rawSql`. Inherits `audit_log`'s existing 90-day `summarize_to_table` retention for free. |
| Dedicated `job_run_metrics` table             | Rejected   | Cleaner isolation + own retention, but a whole new table + migration + retention-policy row for what `audit_log` already models. Operator chose the lighter footprint.                                                                            |
| Prometheus gauge per `(job, key)`             | Rejected   | Arbitrary per-job keys → unbounded cardinality; gauges are last-value so per-run history collapses (two fires between scrapes lose one); and last-run-ok/age already ship as exporter gauges off `job_run_state`.                                 |

The `audit_log` table stays a **seam**: if a specific metric later needs
Prometheus alerting, `metrics_exporter` can read `job_run` rows the same way
it already reads `job_run_state` for `poindexter_scheduler_job_last_run_age_seconds`.
Nothing here forecloses that.

### Fork 2 — first-consumer panel: sink + documented pattern only (chosen)

No dashboard panel is added in this change. The task's motivating example
cited `gate_universe` / `gate_eligible` / `posts_skipped_by_gate` from
**draft PR #2415** (Dev.to selective syndication) — those metric keys are
**not merged** (commit `5b7e24ac7` landed only the settings + parse helpers;
the gate wiring is a later task of that in-flight PR, worked by another
session). Building a panel on keys that don't emit yet would show "No data"
and erode dashboard trust (the reason the Revenue board was parked). So this
change ships the **generic sink + a documented reusable panel snippet**; the
Dev.to panel — and any other consumer panel — is a one-line `rawSql` addition
owned by the consuming PR once its metrics actually flow.

## Design

### Write path (`plugins/scheduler.py`)

In `_runner`, time the job invocation and, after `result` is computed, call a
new best-effort helper:

```python
started = time.perf_counter()
result = await PluginScheduler._invoke_job_with_activity(...)
duration_ms = int((time.perf_counter() - started) * 1000)
...
await self._record_last_run(job.name, ok=bool(result.ok))
await self._capture_job_metrics(job.name, result, duration_ms)
```

`_capture_job_metrics`:

- **Gate:** returns immediately if `result.metrics` is empty (metric-less
  jobs already have `job_run_state` + failure escalation; writing their fires
  would only bloat `audit_log`) or if the master switch is off.
- **Master switch:** `scheduler_job_metrics_capture_enabled` (default `true`),
  read off `self._site_config` when present — mirrors `atom_runs_capture_enabled`.
- **Write:** `await AuditLogger(self._pool).log("job_run", job_name, details, severity="info")`.
  Uses the scheduler's own `self._pool` (no dependency on the global
  `AuditLogger` singleton being initialised), mirroring how `_record_last_run`
  writes directly. `AuditLogger.log` already swallows its own DB errors and
  sanitises non-finite floats; the helper additionally wraps construction in
  try/except so telemetry can never crash the scheduler loop (same discipline
  as `_record_last_run` / `_escalate_job_failure`).
- **Severity `info`** keeps these rows below the `findings_alert_router`
  waterline (it consumes only `warn`/`critical`), so they never page anyone —
  job _failures_ are already escalated separately by `_escalate_job_failure`.

The exception branch of `_runner` (a job that _raised_) has no `result`, so it
writes nothing — correct, there are no metrics to record. A job that returns
`ok=False` **with** metrics still records (the `ok:false` envelope is captured
in `details`); the gate is on `metrics`, independent of `ok`.

### `details` shape

```json
{
  "ok": true,
  "changes_made": 2,
  "duration_ms": 1234,
  "metrics": { "posts_attempted": 3, "posts_crossposted": 2, "errors": 0 }
}
```

Job-emitted keys are namespaced under `details.metrics` so a job can't shadow
the envelope fields. `duration_ms` is a deliberate small addition: it is the
one _universal_ metric every job has, currently unmeasured anywhere, and it
makes the run-history genuinely useful (per-job latency trend) at the cost of
two lines.

### Read path — reusable panel pattern (documented, not applied)

Any future job gets a Grafana panel by copy-editing this `rawSql` against the
`local-brain-db` (`grafana-postgresql-datasource`) datasource:

```sql
-- one series per metric key
SELECT
  timestamp AS "time",
  (details -> 'metrics' ->> 'posts_crossposted')::numeric AS "crossposted"
FROM audit_log
WHERE event_type = 'job_run'
  AND source = 'crosspost_to_devto'
  AND $__timeFilter(timestamp)
ORDER BY timestamp;

-- job latency (universal, from the envelope)
SELECT
  timestamp AS "time",
  (details ->> 'duration_ms')::numeric AS "duration_ms"
FROM audit_log
WHERE event_type = 'job_run'
  AND source = '<job_name>'
  AND $__timeFilter(timestamp)
ORDER BY timestamp;
```

Indexed by `idx_audit_log_event_type` + `idx_audit_log_timestamp`.

## Volume & retention

44 metric-emitting jobs at mixed cadences ≈ **1–3k `job_run` rows/day**.
`audit_log` already carries a `summarize_to_table` retention policy (90-day
raw window, then per-day summary rows), so long-term growth is bounded with no
new policy. The master switch is the escape hatch if raw volume ever needs to
be cut.

## Testing

Unit tests in `tests/unit/plugins/` (new `test_scheduler_job_metrics.py`):

1. metrics-emitting job (`ok=True`) → one `job_run` row; `details.metrics`
   matches, envelope carries `ok/changes_made/duration_ms`.
2. `ok=False` **with** metrics → row still written, `details.ok is False`.
3. empty `metrics` → **no** row (gate).
4. job that raises → no `job_run` row (no result), and the existing
   failure-escalation path is unaffected.
5. master switch off → no row.

The `AuditLogger` write is verified with a fake pool/logger (the existing
scheduler unit tests already use a fake pool — follow their fixture style).

## Out of scope

- **No edits to `crosspost_to_devto.py`** — draft PR #2415 owns that file.
- **No dashboard JSON changes** — panel wiring belongs to consuming PRs.
- No Prometheus gauges (the `audit_log` seam keeps that option open later).

## Routing

- **Issue:** filed on `Glad-Labs/poindexter` (scheduler/plugins are public
  substrate).
- **PR:** against `origin` (`Glad-Labs/glad-labs-stack`), body `Closes
Glad-Labs/poindexter#853`, auto-mirrors to the public repo.

## Task breakdown

1. Add `scheduler_job_metrics_capture_enabled` default to `settings_defaults.py`.
2. Write failing unit tests (`test_scheduler_job_metrics.py`).
3. Implement `_capture_job_metrics` + `duration_ms` timing + wiring in `_runner`.
4. Add a short doc (`docs/architecture/job-run-metrics.md`) covering the sink
   contract + the reusable panel pattern; link it where the scheduler is
   documented.
5. Run the scheduler unit suite green; lint/type-check the touched files.
6. Commit, push to `origin`, open PR closing the issue.
