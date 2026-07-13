# Job-run metrics sink — `JobResult.metrics` → Grafana

Every scheduled job (`plugins/scheduler.py`, driven off `plugins/registry.py`
`_SAMPLES`) returns a `plugins.job.JobResult` with an optional `metrics` dict —
a job's own custom counters (`crosspost_to_devto` → `posts_attempted` /
`posts_crossposted` / `errors`; a link-checker → `broken` / `fixed`; etc.).

Historically the scheduler's `_runner` logged `ok` / `detail` / `changes_made`
and UPSERTed a last-run row into `job_run_state`, but **discarded
`result.metrics`** — so a job's custom metrics reached Loki log lines and
nothing else. This sink (Glad-Labs/poindexter#853) makes them queryable in
Grafana for the whole job fleet.

## Write path

`PluginScheduler._capture_job_metrics` runs once per fire, right after
`_record_last_run`, and writes an `audit_log` row via the canonical
`AuditLogger`:

| Column       | Value                                                             |
| ------------ | ----------------------------------------------------------------- |
| `event_type` | `job_run`                                                         |
| `source`     | the job name (e.g. `crosspost_to_devto`)                          |
| `severity`   | `info` — below the `findings_alert_router` waterline, never pages |
| `details`    | `{ok, changes_made, duration_ms, metrics: {…}}` (jsonb)           |

Job-emitted keys are namespaced under `details.metrics` so they can't shadow
the `ok` / `changes_made` / `duration_ms` envelope. `duration_ms` is timed
around the job invocation — the one universal metric every job has.

### What's recorded, what isn't

- **Only metrics-emitting fires.** A metric-less job already has its last-run
  in `job_run_state` and its failures escalated by `_escalate_job_failure`;
  writing those fires would only bloat the canonical audit trail. The gate is
  on `result.metrics` alone, so an `ok=False` fire that _still_ produced
  telemetry is recorded (with `ok:false` captured).
- **A job that raised writes nothing** — the runner's exception branch has no
  `result`, so there are no metrics to record. Job crashes surface through the
  separate failure-escalation path, not here.
- Best-effort: the write never raises into the scheduler loop (same discipline
  as `_record_last_run`).

### Master switch & retention

`app_settings.scheduler_job_metrics_capture_enabled` (default `true`, mirrors
`atom_runs_capture_enabled`) gates the write. `job_run` rows inherit
`audit_log`'s existing `summarize_to_table` retention (90-day raw window), so
no new retention policy is needed; the switch is the escape hatch if raw volume
ever needs cutting (~1–3k rows/day across the current metrics-emitting jobs).

## Read path — reusable Grafana panel pattern

Any job gets a panel by copy-editing this `rawSql` against the **Local Brain
DB** (`grafana-postgresql-datasource`, uid `local-brain-db`) — the same
datasource Findings and QA-Rails query. Backed by `idx_audit_log_event_type` +
`idx_audit_log_timestamp`.

```sql
-- One timeseries per metric key
SELECT
  timestamp AS "time",
  (details -> 'metrics' ->> 'posts_crossposted')::numeric AS "crossposted",
  (details -> 'metrics' ->> 'posts_attempted')::numeric  AS "attempted",
  (details -> 'metrics' ->> 'errors')::numeric           AS "errors"
FROM audit_log
WHERE event_type = 'job_run'
  AND source = 'crosspost_to_devto'
  AND $__timeFilter(timestamp)
ORDER BY timestamp;
```

```sql
-- Job latency (universal envelope metric — no per-job knowledge needed)
SELECT
  timestamp AS "time",
  (details ->> 'duration_ms')::numeric AS "duration_ms"
FROM audit_log
WHERE event_type = 'job_run'
  AND source = '<job_name>'
  AND $__timeFilter(timestamp)
ORDER BY timestamp;
```

Adding a consumer panel is a JSON-only change to the relevant dashboard (e.g.
Integrations & Admin) and belongs to the PR that introduces or owns the metric
— not to this sink.

## Why `audit_log`, not a new table or Prometheus

- A dedicated `job_run_metrics` table is cleaner in isolation but duplicates
  what `audit_log` already models (and its retention). `audit_log` already has
  the exact precedent: Findings (`event_type='finding'`) and QA-Rails
  (`event_type='qa_pass_completed'`) are read the same way.
- Prometheus gauges would give native alerting but collapse per-run history
  (gauges are last-value) and risk unbounded cardinality across arbitrary
  per-job metric keys. Last-run-ok/age already ship as exporter gauges off
  `job_run_state` (`poindexter_scheduler_job_last_run_age_seconds`).

`audit_log` stays a **seam**: if a specific metric later needs Prometheus
alerting, `metrics_exporter` can read `job_run` rows exactly as it already
reads `job_run_state`.
