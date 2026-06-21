# Relocate scheduler run/status state out of `app_settings` → `job_run_state`

**Date:** 2026-06-21
**Status:** Design approved, pending spec review
**Lineage:** Deferred from the 2026-06-21 `app_settings` audit. PR #1813 handled
the _dead_-key cleanup + shipped the `settings_seed_drift_lint` CI guard; this is
the _normalization_ follow-up it explicitly punted.

## Problem

The in-process job scheduler (`plugins/scheduler.py`) writes two `app_settings`
rows **every time a job fires**:

- `plugin_job_last_run_<job>` — Unix epoch (string) of the last fire.
- `plugin_job_last_status_<job>` — `"ok"` / `"err"` of the last fire.

That is mutable **runtime state**, not configuration. Storing it in `app_settings`
(the config table) conflates config with state and pollutes the settings/audit
surface: dozens of rows sit in the same table operators tune knobs in, they show
up in the generated public settings reference, and the audit script has to carve
out a whole `RUNTIME-STATE` bucket to keep them from reading as dead. (Exact count
is install-dependent: the 2026-06-21 audit observed ~26 live; the baseline seeds
~25 jobs × 2 = ~50 rows; a long-running prod has one pair per _registered_ job,
~37 × 2. All of the relocation/delete logic below is `LIKE`-pattern based, so it
is count-agnostic.)

Goal: move this per-job run/status state into a dedicated `job_run_state` table,
repoint the scheduler's read + write, and keep `app_settings` config-only. This
is a normalization, not a bug fix — the rows are live and harmless today.

### Scope decision (operator-approved 2026-06-21)

Two options were on the table; the operator chose the broader one:

> **Relocate + make the metric real** — also emit the Prometheus scheduler
> freshness metric (long documented but never implemented) from the new table,
> and switch the System Health dashboard panels onto it.

This aligns with `feedback_grafana_everything` ("every metric gets a panel"). The
job-failure _alerting_ surface is **out of scope**: failures already escalate via
`PluginScheduler._escalate_job_failure` → `emit_finding` → the findings/alert
pipeline. The new gauges are for dashboard visibility, not a new alert rule.

## Non-goals

- No change to scheduling behavior (restart-survival anchoring, first-fire
  stagger, cron/interval parsing all stay byte-for-byte).
- No new Prometheus alert rules.
- No change to `app_settings` for _job config_ (`plugin.job.<name>` rows stay —
  those ARE config).
- No backfill of historical per-fire history (the table holds only the _latest_
  run/status per job, exactly like today).

## Consumer map (verified by grep across the whole repo)

| #   | Consumer                                                                            | Role                                                                                                | Disposition                                                                               |
| --- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1   | `plugins/scheduler.py::_record_last_run`                                            | **writer** (2 keys/fire)                                                                            | repoint → one `job_run_state` UPSERT                                                      |
| 2   | `plugins/scheduler.py::_persisted_last_run_epoch`                                   | **reader, load-bearing** (restart-survival anchoring, poindexter#561)                               | repoint → read `last_run_at`, return `.timestamp()`                                       |
| 3   | `services/migrations/0000_baseline.seeds.sql`                                       | **seeds ~50 rows** (`run='0'`, `status='ok'`)                                                       | scrub the INSERT lines                                                                    |
| 4   | `infrastructure/grafana/dashboards/system-health-merged.json` (panel ~line 1489)    | **live Grafana panel** (rawSql self-join on `app_settings`)                                         | replace with Prometheus-metric panels                                                     |
| 5   | `services/metrics_exporter.py`                                                      | exporter — does **not** yet emit a scheduler metric                                                 | add two gauges sourced from `job_run_state`                                               |
| 6   | `scripts/_dashboard_additions.py`                                                   | one-shot dashboard applier; its three scheduler panels target a Prometheus metric that didn't exist | fix docstring; its panels become real                                                     |
| 7   | `tests/unit/.../test_plugin_job_last_run_seeds_zero.py`                             | asserts the seeds **exist** + are `'0'`                                                             | replace with a **seeds-removed** regression test                                          |
| 8   | `tests/unit/plugins/test_scheduler_telemetry.py`                                    | unit tests of write/read against `app_settings`                                                     | rewrite for `job_run_state`                                                               |
| 9   | `tests/integration/test_plugin_scheduler.py::test_record_last_run_writes_telemetry` | integration test of the write                                                                       | rewrite for `job_run_state`                                                               |
| 10  | `tests/unit/services/test_metrics_exporter.py`                                      | exporter unit tests                                                                                 | add coverage for the new gauges                                                           |
| 11  | `scripts/ci/settings_audit.py` (`RUNTIME-STATE` bucket)                             | read-only classifier                                                                                | **no change** — will report `RUNTIME-STATE: 0` after the move (safety net for stragglers) |
| 12  | `scripts/check_run_taps.py`                                                         | throwaway debug one-off                                                                             | **no change** (per `feedback_scripts_vs_services`)                                        |
| 13  | `src/cofounder_agent/console/js/settings-data.js`                                   | console settings UI                                                                                 | **no change** — zero references (verified); task's worry unfounded                        |
| 14  | `docs/reference/app-settings.md`                                                    | generated doc (nightly `regen-app-settings-doc.yml`)                                                | **no in-PR edit** — see "Docs" below                                                      |

Confirmed clean (do NOT seed these): `brain/seed_app_settings.json` (0 matches),
`settings_defaults.py` (0 matches). `0000_baseline.seeds.sql` is the **only**
seed source.

## Approaches considered

- **A — dedicated `job_run_state` table, `timestamptz` columns (CHOSEN).**
  Native types, collapses the Grafana self-join into a flat select, clean
  separation of state from config.
- **B — reuse `atom_runs` / `capability_outcomes`.** Rejected: wrong grain
  (per-atom pipeline telemetry, not per-job scheduler state).
- **C — keep in `app_settings` under a new `category`.** Rejected: still in the
  config table — defeats the entire goal.
- **Representation sub-choice — mirror the current epoch as `bigint` vs.
  `timestamptz`.** Chose `timestamptz`: native, and the read path converts back
  to a float epoch via `.timestamp()` so `_interval_next_run` is untouched.

## Detailed design

### New table

```sql
CREATE TABLE IF NOT EXISTS job_run_state (
    job_name    text PRIMARY KEY,
    last_run_at timestamptz,
    last_status text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
```

No extra index (PK lookup by `job_name`; the dashboard scans ~37 rows). No FK to
job names — jobs are code/entry-point defined, not a DB table; stale rows for
removed jobs are harmless (could be pruned later — YAGNI now).

### Migration `YYYYMMDD_HHMMSS_create_job_run_state_table.py`

Schema DDL + a one-time **data** mutation (relocating state) — both allowed in a
migration file per repo convention (seeds, by contrast, must NOT go in
migrations; there are none here). `up()` runs three statements in order:

1. **DDL** — the `CREATE TABLE IF NOT EXISTS` above.
2. **Backfill** from the existing rows:

```sql
INSERT INTO job_run_state (job_name, last_run_at, last_status, updated_at)
SELECT
    substring(r.key FROM (length('plugin_job_last_run_') + 1))            AS job_name,
    CASE WHEN r.value ~ '^[0-9]+$' AND r.value <> '0'
         THEN to_timestamp(r.value::double precision)
         ELSE NULL END                                                    AS last_run_at,
    s.value                                                               AS last_status,
    now()
FROM app_settings r
LEFT JOIN app_settings s
    ON s.key = 'plugin_job_last_status_'
            || substring(r.key FROM (length('plugin_job_last_run_') + 1))
WHERE r.key LIKE 'plugin_job_last_run_%'
ON CONFLICT (job_name) DO NOTHING;
```

3. **Delete** the relocated rows:

```sql
DELETE FROM app_settings
WHERE key LIKE 'plugin_job_last_run_%'
   OR key LIKE 'plugin_job_last_status_%';
```

`down()` — **documented no-op** (mirrors the `20260620_..._retire_orphaned_ops_triage`
precedent). The table is now load-bearing for the scheduler; reverting would have
to re-seed the `app_settings` keys and reintroduce exactly the pollution this
removes. Forward-only.

**Correctness notes:**

- The `'0'` / empty / non-numeric "never ran" sentinel maps to `last_run_at =
NULL`, NOT `to_timestamp(0)` (1970). `NULL` routes through `_interval_next_run`'s
  never-run branch (staggered first-fire); `to_timestamp(0)` would read as
  "overdue by 56 years" → fire-immediately stampede on a fresh install.
- The backfill regex is `'^[0-9]+$'` (values are `str(int(time.time()))`, always
  integer strings) — no backslashes, so no asyncpg escaping hazard.
- **Ordering is what makes this safe on both install paths.** Migrations run at
  worker startup _before_ the scheduler registers jobs, so the table exists +
  is backfilled before any `_persisted_last_run_epoch` read — no read-fallback
  shim is needed (the backfill _is_ the shim). Fresh installs: seeds removed →
  no rows to backfill → empty table → correct never-run semantics.

### `plugins/scheduler.py`

`_record_last_run(name, ok)` — replace the two `app_settings` writes with one
UPSERT:

```python
status = "ok" if ok else "err"
sql = (
    "INSERT INTO job_run_state (job_name, last_run_at, last_status, updated_at) "
    "VALUES ($1, now(), $2, now()) "
    "ON CONFLICT (job_name) DO UPDATE SET "
    "last_run_at = EXCLUDED.last_run_at, "
    "last_status = EXCLUDED.last_status, "
    "updated_at = now()"
)
try:
    async with self._pool.acquire() as conn:
        await conn.execute(sql, name, status)
except Exception as e:
    logger.warning("scheduler: last-run telemetry write failed for %r: %s", name, e)
```

`_persisted_last_run_epoch(job_name)` — read the timestamp, convert to epoch
(keep the name + `float | None` return so `_interval_next_run` is untouched):

```python
async with self._pool.acquire() as conn:
    val = await conn.fetchval(
        "SELECT last_run_at FROM job_run_state WHERE job_name = $1", job_name,
    )
return val.timestamp() if val is not None else None
```

Update the module docstring (lines ~13–15) and the inline docstrings on these two
methods + the `_interval_next_run` comment block (~line 226) that cite
`app_settings` / `plugin_job_last_run_*`. Telemetry-write failure stays swallowed
(observability must not crash the scheduler loop).

### `services/metrics_exporter.py`

Two new module-level gauges:

```python
SCHEDULER_JOB_LAST_RUN_AGE_SECONDS = Gauge(
    "poindexter_scheduler_job_last_run_age_seconds",
    "Seconds since each scheduled plugin job last fired (from job_run_state). "
    "Absent for never-run jobs.",
    ["job_name"],
)
SCHEDULER_JOB_LAST_RUN_OK = Gauge(
    "poindexter_scheduler_job_last_run_ok",
    "1 if the job's most recent fire returned ok, 0 if it errored "
    "(from job_run_state.last_status). Absent for never-run jobs.",
    ["job_name"],
)
```

A self-contained block in `refresh_metrics()` (own `try/except` +
`_note_refresh_error("job_run_state", e)`), clear-then-repopulate so a removed
job's series drops out; never-run rows (`last_run_at IS NULL`) emit nothing:

```python
SCHEDULER_JOB_LAST_RUN_AGE_SECONDS.clear()
SCHEDULER_JOB_LAST_RUN_OK.clear()
try:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT job_name, "
            "EXTRACT(EPOCH FROM (now() - last_run_at)) AS age_s, last_status "
            "FROM job_run_state WHERE last_run_at IS NOT NULL"
        )
    for r in rows:
        SCHEDULER_JOB_LAST_RUN_AGE_SECONDS.labels(job_name=r["job_name"]).set(
            float(r["age_s"])
        )
        if r["last_status"] is not None:
            SCHEDULER_JOB_LAST_RUN_OK.labels(job_name=r["job_name"]).set(
                1 if r["last_status"] == "ok" else 0
            )
except Exception as e:
    _note_refresh_error("job_run_state", e)
```

Cardinality is bounded: ~37 jobs × 2 gauges ≈ 74 series (`job_name` is a bounded
label set — not a raw-path cardinality bomb). Add a metric line to the exporter's
module docstring under a new "Scheduler" heading.

### Grafana — `system-health-merged.json`

Replace the rawSql freshness panel (the `app_settings` self-join) with the
Prometheus-backed panels the generator already defines and which now have a real
metric behind them:

- **Scheduler freshness table** — `poindexter_scheduler_job_last_run_age_seconds`
  - `..._ok` (instant, table format; `job_name` → rows).
- **Top-10 stalest jobs** — `topk(10, poindexter_scheduler_job_last_run_age_seconds)`.
- **Failed jobs count** — `count(poindexter_scheduler_job_last_run_ok == 0)`.

Implementation choice (resolve in the plan): either re-run the idempotent
`scripts/_dashboard_additions.py` (its designed workflow — id-based replace) or
hand-apply the three panel dicts. Re-running is the intended path but touches
sibling panels; the plan will pick one and verify the JSON diff is limited to the
scheduler panels.

### `scripts/_dashboard_additions.py`

Fix the `system_health_scheduler_freshness_table` docstring: the metric is now
sourced from `job_run_state`, not "app*settings.plugin_job_last_run*<name>". No
logic change (the `expr` already targets the metric).

## Tests

- **Rewrite** `tests/unit/plugins/test_scheduler_telemetry.py`:
  - `_record_last_run` → assert a single UPSERT into `job_run_state` with
    `(name, status)` params (was: two `app_settings` execs).
  - `_persisted_last_run_epoch` + `_interval_next_run` → the `_pool_with_fetchval`
    stub returns a `datetime` (timestamptz) / `None` instead of an epoch string;
    the never-run, overdue, and not-due branches stay asserted.
- **Rewrite** `tests/integration/test_plugin_scheduler.py::test_record_last_run_writes_telemetry`
  → query `job_run_state` (job_name, last_run_at, last_status) instead of the two
  `app_settings` keys; keep the re-run-flips-status / bumps-timestamp assertions.
- **Replace** `tests/unit/.../test_plugin_job_last_run_seeds_zero.py` with a
  **seeds-removed** regression test (mirror `test_drive_media_gates_seed_removed.py`):
  assert no `plugin_job_last_run_` / `plugin_job_last_status_` key appears in
  `0000_baseline.seeds.sql` or `settings_defaults.py`.
- **Add** to `tests/unit/services/test_metrics_exporter.py`: the two new gauges to
  `_reset_gauges`, and a refresh test feeding a `job_run_state` `fetch` response
  (one ok job, one err job, one never-run) → assert age/ok series for the first
  two and absence for the never-run one.
- **Migration coverage**: exercised end-to-end by `migrations_smoke.py` (a
  required CI check — applies cleanly to a fresh DB). Add a focused
  `integration_db` test for the backfill (`'0'`→NULL, real epoch→timestamp,
  status join) + the `app_settings` delete **if** a single-migration harness
  exists; otherwise rely on migrations-smoke + the seeds-removed unit test.

## Docs

- `docs/reference/app-settings.md` — **not edited in this PR.** It's generated
  from a _fresh baseline+migrations DB_ by the nightly `regen-app-settings-doc.yml`
  (schedule/dispatch only — NOT a PR gate, so it won't block this PR). After merge
  - the migration applying, its next run drops the ~50 rows and opens its own
    reconciliation PR — exactly the "a setting was removed" path it's built for.
    Call this out in the PR body.
- `CLAUDE.md` "Database tables" — optional one-line entry for `job_run_state`.
  The repo-derived stats (table counts, migration narrative) auto-sync via the
  `sync-claude-md.yml` workflow; a manual one-liner is optional, not required.

## Risks & mitigations

- **Reseed-drift** (the headline risk): if the seed scrub is forgotten, the
  every-boot `INSERT … ON CONFLICT DO NOTHING` resurrects the deleted keys.
  Mitigated by (a) scrubbing `0000_baseline.seeds.sql`, (b) the seeds-removed
  regression test, (c) the existing `settings_seed_drift_lint` CI guard. Note the
  guard can't enumerate keys from a `LIKE`-pattern DELETE, so the seed scrub is
  the primary defense, not the guard.
- **Hidden exact-count assertion** on `app_settings` / seed rows somewhere in the
  suite — caught by the full unit run during verification.
- **Forward-only**: `down()` is intentionally non-reversible (documented).
- **Dashboard regen side-effects** if `_dashboard_additions.py` is re-run —
  verify the committed JSON diff is limited to scheduler panels.

## Verification plan

- `poetry run pytest tests/unit/plugins/test_scheduler_telemetry.py
tests/unit/services/test_metrics_exporter.py
tests/unit/services/migrations/ -q` (+ the integration scheduler test where the
  real-services harness is available).
- `python scripts/ci/migrations_smoke.py` — fresh-DB apply (the new table +
  backfill + delete; no-op on the empty fresh DB).
- `python scripts/ci/migrations_lint.py` and `python scripts/ci/settings_seed_drift_lint.py`
  — clean.
- `python scripts/ci/settings_audit.py db_full.tsv` against a prod dump after the
  migration applies → `RUNTIME-STATE: 0`, the keys gone from the table.
- Visual: System Health dashboard scheduler panels render from the Prometheus
  metric (age + ok per job).

## Out of scope / follow-ups

- Pruning `job_run_state` rows for removed jobs (a retention concern; YAGNI).
- A `SchedulerJobStale` Prometheus alert rule (failure alerting already exists via
  findings; revisit only if dashboard-only visibility proves insufficient).
- Retiring the `RUNTIME-STATE` bucket from `settings_audit.py` (kept as a safety
  net).
