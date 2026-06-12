# SEO Harvest Loop — Milestone B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the SEO harvest loop — auto-enqueue `seo_refresh` tasks from the analyzer's ranked opportunities (gated, capped, sign-off-first) and measure GSC position/CTR delta N days after each refresh.

**Architecture:** Two prerequisite fixes (analyzer upsert status-latch; `refreshed_at` anchor) + two new scheduled jobs (`enqueue_seo_refreshes`, `measure_seo_refresh_outcomes`) + a new setting + Grafana panels. All read/write only local Postgres tables; no new Google API calls. Jobs follow the existing `services/jobs/run_seo_opportunity_analyzer.py` pattern (a class with `name`/`description`/`schedule`/`idempotent`/`async run(pool, config)` returning `JobResult`), registered in `plugins/registry.py` `_SAMPLES`.

**Tech Stack:** Python 3 (async/await), asyncpg, Postgres, the in-house plugin-job framework (`plugins/job.py::JobResult`), `services/tasks_db.py::TasksDatabase.add_task`, `utils/findings.py::emit_finding`, Grafana file-provisioned dashboard JSON.

**Spec:** [`docs/superpowers/specs/2026-06-12-seo-harvest-milestone-b-design.md`](../specs/2026-06-12-seo-harvest-milestone-b-design.md)

**Test command (all tasks):** from `src/cofounder_agent` —
`poetry run pytest <path> -q`. (If the worktree venv is fresh, `poetry install` in `src/cofounder_agent` once first.) Integration-DB tests (`tests/integration_db/`) require a live Postgres and run in CI's `integration-db` job; run locally only if a test DB is available.

---

## File Structure

| File                                                                                 | Responsibility                    | Action                    |
| ------------------------------------------------------------------------------------ | --------------------------------- | ------------------------- |
| `src/cofounder_agent/services/migrations/<ts>_add_seo_opportunities_refreshed_at.py` | Add `refreshed_at` column         | Create                    |
| `src/cofounder_agent/services/seo/striking_distance.py`                              | Analyzer upsert — status latch    | Modify (`_UPSERT_SQL`)    |
| `src/cofounder_agent/modules/content/atoms/content_republish_post.py`                | Stamp `refreshed_at` on republish | Modify (`_STAMP_OPP_SQL`) |
| `src/cofounder_agent/services/settings_defaults.py`                                  | `max_per_run` + 2 job-config rows | Modify                    |
| `src/cofounder_agent/services/jobs/enqueue_seo_refreshes.py`                         | J1 — auto-enqueue                 | Create                    |
| `src/cofounder_agent/services/jobs/measure_seo_refresh_outcomes.py`                  | J2 — outcome measurement          | Create                    |
| `src/cofounder_agent/plugins/registry.py`                                            | Register both jobs                | Modify (`_SAMPLES`)       |
| `infrastructure/grafana/dashboards/seo-harvest.json`                                 | Queue + outcome panels            | Modify                    |
| `docs/architecture/seo-harvest-loop.md`                                              | Status update                     | Modify                    |
| `src/cofounder_agent/tests/unit/services/jobs/test_enqueue_seo_refreshes.py`         | J1 unit tests                     | Create                    |
| `src/cofounder_agent/tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py`  | J2 unit tests                     | Create                    |
| `src/cofounder_agent/tests/unit/seo/test_seo_refresh_settings.py`                    | `max_per_run` default             | Modify                    |
| `src/cofounder_agent/tests/unit/seo/test_content_republish_post.py`                  | `refreshed_at` stamp              | Modify                    |
| `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py`                 | status-latch + `refreshed_at` col | Modify                    |

---

## Task 1: Migration — add `refreshed_at` column

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260612_050000_add_seo_opportunities_refreshed_at.py`
- Test (modify): `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py`

- [ ] **Step 1: Write the migration** (light imports only — stdlib `logging` — so the `migrations-smoke` CI step applies it without a full app boot).

```python
"""Migration: add seo_opportunities.refreshed_at.

SEO Harvest Loop Phase 2c (#763). The outcome-measurement job
(measure_seo_refresh_outcomes) gates "measure N days after the refresh" on a
refresh timestamp. detected_at is bumped every analyzer run (not a refresh
anchor) and outcome_measured_at is the measurement time — neither marks when a
refresh happened. content.republish_post stamps this column alongside the
baseline + status='refreshed'.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ADD_COLUMN = (
    "ALTER TABLE seo_opportunities "
    "ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_ADD_COLUMN)
    logger.info(
        "Migration add_seo_opportunities_refreshed_at: refreshed_at column added"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE seo_opportunities DROP COLUMN IF EXISTS refreshed_at"
        )
    logger.info("Migration add_seo_opportunities_refreshed_at down: reverted")
```

- [ ] **Step 2: Lint the migration**

Run (from repo root): `python scripts/ci/migrations_lint.py`
Expected: PASS (no collisions, runner interface present — `up`/`down` async fns).

- [ ] **Step 3: Add `refreshed_at` to the integration_db column-existence test**

In `tests/integration_db/test_seo_opportunities.py`, add `"refreshed_at"` to the expected-columns tuple in `test_seo_opportunities_table_exists_with_expected_columns` (after `"outcome_measured_at"`):

```python
        "outcome_measured_at",
        "refreshed_at",
```

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260612_050000_add_seo_opportunities_refreshed_at.py src/cofounder_agent/tests/integration_db/test_seo_opportunities.py
git commit -F -   # message: "feat(seo): add seo_opportunities.refreshed_at for outcome measurement (#763)"
```

---

## Task 2: Analyzer upsert — preserve non-open status (P1)

**Files:**

- Modify: `src/cofounder_agent/services/seo/striking_distance.py` (`_UPSERT_SQL`, ~lines 113-127)
- Test (modify): `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py`

- [ ] **Step 1: Write the failing integration_db test** (real DB — the behavior is SQL-level). Append to `tests/integration_db/test_seo_opportunities.py`:

```python
async def test_upsert_preserves_non_open_status(test_pool):
    """A queued/refreshed/dismissed row must NOT be flipped back to 'open' by the
    daily analyzer upsert, or auto-enqueue would re-refresh it forever (#763)."""
    from services.seo.striking_distance import DEFAULT_THRESHOLDS, analyze_and_upsert

    post_id = uuid4()
    slug = f"seo-latch-{post_id.hex[:8]}"
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (id, title, slug, status, published_at) "
                "VALUES ($1, 'Latch Test', $2, 'published', NOW())",
                post_id, slug,
            )
            await conn.execute(
                "INSERT INTO post_performance "
                "(post_id, slug, google_impressions, google_clicks, "
                " google_avg_position, measured_at) "
                "VALUES ($1, $2, 500, 4, 6.0, NOW())",
                post_id, slug,
            )
        await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        # Simulate a refresh having happened.
        async with test_pool.acquire() as conn:
            await conn.execute(
                "UPDATE seo_opportunities "
                "SET status='refreshed', baseline_position=current_position, "
                "    baseline_ctr=ctr, refreshed_at=NOW() WHERE post_id=$1",
                post_id,
            )
        # The analyzer runs again the next day.
        await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        async with test_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, baseline_position FROM seo_opportunities "
                "WHERE post_id=$1", post_id,
            )
        assert row["status"] == "refreshed", "status latch failed — reverted to open"
        assert row["baseline_position"] is not None, "baseline lost on re-upsert"
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute("DELETE FROM seo_opportunities WHERE post_id=$1", post_id)
            await conn.execute("DELETE FROM post_performance WHERE post_id=$1", post_id)
            await conn.execute("DELETE FROM posts WHERE id=$1", post_id)
```

- [ ] **Step 2: Run it to verify it fails** (if a test DB is available)

Run: `poetry run pytest tests/integration_db/test_seo_opportunities.py::test_upsert_preserves_non_open_status -q`
Expected: FAIL — `status` reverts to `'open'` (the current upsert forces it). _If no test DB is available locally, rely on CI's `integration-db` job; proceed to Step 3._

- [ ] **Step 3: Apply the status-latch fix** in `services/seo/striking_distance.py`. Replace the `status = 'open',` line inside `_UPSERT_SQL`'s `DO UPDATE SET` with the latch:

```python
_UPSERT_SQL = """
INSERT INTO seo_opportunities
    (post_id, slug, target_query, tier, current_position,
     impressions, ctr, gap_score, status, detected_at)
VALUES ($1, $2, '', $3, $4, $5, $6, $7, 'open', NOW())
ON CONFLICT (post_id, target_query) DO UPDATE
    SET tier             = EXCLUDED.tier,
        slug             = EXCLUDED.slug,
        current_position = EXCLUDED.current_position,
        impressions      = EXCLUDED.impressions,
        ctr              = EXCLUDED.ctr,
        gap_score        = EXCLUDED.gap_score,
        status           = CASE
            WHEN seo_opportunities.status IN ('queued','refreshed','dismissed')
            THEN seo_opportunities.status
            ELSE 'open'
        END,
        detected_at      = NOW()
"""
```

- [ ] **Step 4: Run the test to verify it passes** (if a test DB is available)

Run: `poetry run pytest tests/integration_db/test_seo_opportunities.py -q`
Expected: PASS (both the new test and the existing `test_analyze_classifies_and_upserts` — an `open` row still lands `open`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/seo/striking_distance.py src/cofounder_agent/tests/integration_db/test_seo_opportunities.py
git commit -F -   # "fix(seo): analyzer upsert preserves queued/refreshed/dismissed status (#763)"
```

---

## Task 3: Republish atom — stamp `refreshed_at` (P2)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/content_republish_post.py` (`_STAMP_OPP_SQL`, ~lines 81-87)
- Test (modify): `src/cofounder_agent/tests/unit/seo/test_content_republish_post.py`

- [ ] **Step 1: Strengthen the failing unit test.** In `test_republish_updates_meta_exports_and_stamps`, change the `UPDATE seo_opportunities` capture to record the SQL, and assert `refreshed_at` is stamped. Replace the `elif "UPDATE seo_opportunities" in sql:` branch body and the stamp assertion:

```python
            elif "UPDATE seo_opportunities" in sql:
                calls["stamp"] = (sql, args)
```

and after `assert calls["stamp"] is not None`:

```python
    stamp_sql, _ = calls["stamp"]
    assert "refreshed_at" in stamp_sql.lower(), "refresh timestamp not stamped"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/unit/seo/test_content_republish_post.py::test_republish_updates_meta_exports_and_stamps -q`
Expected: FAIL — `refreshed_at` not in the stamp SQL.

- [ ] **Step 3: Add `refreshed_at = NOW()` to `_STAMP_OPP_SQL`** in `content_republish_post.py`:

```python
_STAMP_OPP_SQL = """
UPDATE seo_opportunities
   SET status            = 'refreshed',
       baseline_position = current_position,
       baseline_ctr      = ctr,
       refreshed_at      = NOW()
 WHERE id = $1::uuid
"""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/unit/seo/test_content_republish_post.py -q`
Expected: PASS (all 3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_republish_post.py src/cofounder_agent/tests/unit/seo/test_content_republish_post.py
git commit -F -   # "feat(seo): republish stamps seo_opportunities.refreshed_at (#763)"
```

---

## Task 4: Settings — `max_per_run` + job-config rows

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (the SEO Harvest Phase 2 block, ~lines 658-671)
- Test (modify): `src/cofounder_agent/tests/unit/seo/test_seo_refresh_settings.py`

- [ ] **Step 1: Write the failing test.** Add to `tests/unit/seo/test_seo_refresh_settings.py`:

```python
def test_max_per_run_default_seeded():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["seo.refresh.max_per_run"] == "3"


def test_milestone_b_job_configs_seeded():
    from services.settings_defaults import DEFAULTS

    assert "plugin.job.enqueue_seo_refreshes" in DEFAULTS
    assert "plugin.job.measure_seo_refresh_outcomes" in DEFAULTS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/unit/seo/test_seo_refresh_settings.py -q`
Expected: FAIL — `KeyError: 'seo.refresh.max_per_run'`.

- [ ] **Step 3: Add the keys** to `settings_defaults.py`. After the `'seo.refresh.outcome_measure_after_days': '14',` line (and before the closing `}` of the Phase-2 block), add:

```python
    # Phase 2b — auto-enqueue cap. Conservative: each refresh still needs
    # operator sign-off at seo_refresh_gate, so default low.
    'seo.refresh.max_per_run': '3',
    # Milestone B job-scheduler rows. NOTE: this `enabled` is the SCHEDULER
    # switch (does the job fire); the content-mutating safety gate is
    # seo.refresh.enabled, read INSIDE enqueue_seo_refreshes (default false), so
    # the enqueue job fires every 6h but no-ops until the operator opts in.
    'plugin.job.enqueue_seo_refreshes': '{"enabled": true, "interval_seconds": 0, "config": {"schedule": "every 6 hours"}}',
    'plugin.job.measure_seo_refresh_outcomes': '{"enabled": true, "interval_seconds": 0, "config": {"schedule": "every 24 hours"}}',
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/unit/seo/test_seo_refresh_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/seo/test_seo_refresh_settings.py
git commit -F -   # "feat(seo): seed seo.refresh.max_per_run + Milestone B job configs (#763)"
```

---

## Task 5: J1 — `enqueue_seo_refreshes` job

**Files:**

- Create: `src/cofounder_agent/services/jobs/enqueue_seo_refreshes.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (`_SAMPLES`, after the analyzer row ~line 672)
- Test (create): `src/cofounder_agent/tests/unit/services/jobs/test_enqueue_seo_refreshes.py`

- [ ] **Step 1: Write the failing tests** in `tests/unit/services/jobs/test_enqueue_seo_refreshes.py`:

```python
"""Unit tests for the seo_refresh auto-enqueue job (no DB — fake pool)."""

from __future__ import annotations

import pytest


def test_job_has_required_attrs():
    from services.jobs.enqueue_seo_refreshes import EnqueueSeoRefreshesJob

    job = EnqueueSeoRefreshesJob()
    assert job.name == "enqueue_seo_refreshes"
    assert isinstance(job.schedule, str) and job.schedule


def test_job_registered_in_core_samples():
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(getattr(j, "name", None) == "enqueue_seo_refreshes" for j in jobs)


class _SC:
    def __init__(self, vals):
        self._v = vals

    def get_bool(self, key, default):
        return self._v.get(key, default)

    def get_float(self, key, default):
        return self._v.get(key, default)


@pytest.mark.asyncio
async def test_noop_when_refresh_disabled():
    from services.jobs.enqueue_seo_refreshes import EnqueueSeoRefreshesJob

    job = EnqueueSeoRefreshesJob()
    res = await job.run(pool=object(), config={"_site_config": _SC({"seo.refresh.enabled": False})})
    assert res.ok is True
    assert "off" in res.detail.lower()


@pytest.mark.asyncio
async def test_enqueues_capped_and_parks(monkeypatch):
    from services.jobs import enqueue_seo_refreshes as mod

    candidates = [
        {"opportunity_id": "opp-1", "post_id": "post-1", "slug": "a", "target_query": "", "gap_score": 900.0},
        {"opportunity_id": "opp-2", "post_id": "post-2", "slug": "b", "target_query": "q", "gap_score": 500.0},
    ]
    parked = []

    class _Conn:
        async def fetch(self, sql, *args):
            # the cap ($1) is honored by the caller passing max_per_run; echo all
            return candidates

        async def execute(self, sql, *args):
            if "UPDATE seo_opportunities" in sql:
                parked.append(args[0])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    added = []

    class _Tasks:
        def __init__(self, pool):
            pass

        async def add_task(self, data):
            added.append(data)
            return f"task-{len(added)}"

    monkeypatch.setattr(mod, "TasksDatabase", _Tasks)
    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)

    job = mod.EnqueueSeoRefreshesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.enabled": True, "seo.refresh.max_per_run": 2})},
    )
    assert res.ok is True
    assert res.changes_made == 2
    # Each enqueued task is a seo_refresh with the post id in metadata.
    assert all(d["template_slug"] == "seo_refresh" for d in added)
    assert {d["task_metadata"]["post_id"] for d in added} == {"post-1", "post-2"}
    # Both opportunities parked to queued.
    assert set(parked) == {"opp-1", "opp-2"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/jobs/test_enqueue_seo_refreshes.py -q`
Expected: FAIL — `ModuleNotFoundError: services.jobs.enqueue_seo_refreshes`.

- [ ] **Step 3: Write the job** `services/jobs/enqueue_seo_refreshes.py`:

```python
"""Scheduled job: auto-enqueue seo_refresh tasks from open SEO opportunities.

SEO Harvest Loop Phase 2b (#763). Picks the top-N open opportunities by
gap_score and creates a gated seo_refresh pipeline_task for each, skipping any
post that already has an ACTIVE refresh task, then parks the opportunity at
status='queued'. Content-MUTATING (creates a gated task) — gates on
seo.refresh.enabled (default off). The approval gate (seo_refresh_gate, ships
enabled) still requires operator sign-off before any republish, so the worst
case of a bug here is an extra task that pauses for review.

Dedup: the task<->post link is pipeline_versions.stage_data->'task_metadata'->>
'post_id' (the seam tasks_db.add_task writes and content_router._load_task_metadata
reads). "Active" is an explicit whitelist of in-flight statuses so a future
status can't slip through as active.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.tasks_db import TasksDatabase
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SELECT_CANDIDATES_SQL = """
SELECT o.id AS opportunity_id, o.post_id, o.slug, o.target_query, o.gap_score
FROM seo_opportunities o
WHERE o.status = 'open'
  AND o.post_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM pipeline_tasks t
      JOIN pipeline_versions v ON v.task_id = t.task_id
      WHERE t.template_slug = 'seo_refresh'
        AND t.status IN ('pending','in_progress','awaiting_gate','awaiting_approval')
        AND v.stage_data->'task_metadata'->>'post_id' = o.post_id::text
  )
ORDER BY o.gap_score DESC
LIMIT $1
"""

_MARK_QUEUED_SQL = "UPDATE seo_opportunities SET status='queued' WHERE id=$1::uuid"


class EnqueueSeoRefreshesJob:
    name = "enqueue_seo_refreshes"
    description = (
        "Auto-enqueue seo_refresh tasks from the top open SEO opportunities "
        "(gated on seo.refresh.enabled; capped by seo.refresh.max_per_run)"
    )
    schedule = "every 6 hours"
    idempotent = False  # creates tasks

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None or not sc.get_bool("seo.refresh.enabled", False):
            return JobResult(ok=True, detail="seo.refresh.enabled is off; skipped")

        max_per_run = int(sc.get_float("seo.refresh.max_per_run", 3))
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_SELECT_CANDIDATES_SQL, max_per_run)
        except Exception as e:  # noqa: BLE001
            logger.warning("[enqueue_seo_refreshes] candidate query failed: %s", e, exc_info=True)
            return JobResult(ok=False, detail=f"candidate query failed: {type(e).__name__}: {e}")

        tasks_db = TasksDatabase(pool)
        queued: list[dict[str, Any]] = []
        for r in rows:
            try:
                task_id = await tasks_db.add_task(
                    {
                        "task_type": "seo_refresh",
                        "template_slug": "seo_refresh",
                        "topic": r["slug"],
                        "status": "pending",
                        "task_metadata": {
                            "post_id": str(r["post_id"]),
                            "seo_opportunity_id": str(r["opportunity_id"]),
                            "target_query": r["target_query"] or "",
                        },
                    }
                )
                async with pool.acquire() as conn:
                    await conn.execute(_MARK_QUEUED_SQL, str(r["opportunity_id"]))
                queued.append(
                    {"slug": r["slug"], "gap_score": float(r["gap_score"] or 0), "task_id": task_id}
                )
            except Exception as e:  # noqa: BLE001 — one bad candidate never aborts the run
                logger.warning("[enqueue_seo_refreshes] enqueue failed for %s: %s", r["slug"], e)

        if queued:
            body = "## SEO refresh — queued for review\n\n" + "\n".join(
                f"- **{q['slug']}** — gap≈{q['gap_score']:.0f} clicks/mo (task {q['task_id']})"
                for q in queued
            )
            emit_finding(
                source="enqueue_seo_refreshes",
                kind="seo_refresh_queued",
                title=f"SEO: {len(queued)} refresh task(s) queued for sign-off",
                body=body,
                dedup_key=None,
                extra={"count": len(queued)},
            )

        logger.info("[enqueue_seo_refreshes] queued %d refresh task(s)", len(queued))
        return JobResult(
            ok=True,
            detail=f"queued {len(queued)} refresh task(s)",
            changes_made=len(queued),
            metrics={"queued": len(queued)},
        )
```

- [ ] **Step 4: Register the job** in `plugins/registry.py` `_SAMPLES`, immediately after the `run_seo_opportunity_analyzer` row (~line 672):

```python
        # SEO Harvest Loop Phase 2b — auto-enqueue seo_refresh tasks from the
        # analyzer's ranked open opportunities. Gated on seo.refresh.enabled
        # (default off); fires every 6h but no-ops until the operator opts in.
        ("jobs", "services.jobs.enqueue_seo_refreshes", "EnqueueSeoRefreshesJob"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/unit/services/jobs/test_enqueue_seo_refreshes.py -q`
Expected: PASS (all 4 tests, incl. `test_job_registered_in_core_samples` which proves the registry import works).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/enqueue_seo_refreshes.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/unit/services/jobs/test_enqueue_seo_refreshes.py
git commit -F -   # "feat(seo): enqueue_seo_refreshes job — gated auto-enqueue (#763)"
```

---

## Task 6: J2 — `measure_seo_refresh_outcomes` job

**Files:**

- Create: `src/cofounder_agent/services/jobs/measure_seo_refresh_outcomes.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (`_SAMPLES`, after the J1 row)
- Test (create): `src/cofounder_agent/tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py`

- [ ] **Step 1: Write the failing tests** in `tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py`:

```python
"""Unit tests for the seo_refresh outcome-measurement job (no DB — fake pool)."""

from __future__ import annotations

import pytest


def test_job_has_required_attrs():
    from services.jobs.measure_seo_refresh_outcomes import MeasureSeoRefreshOutcomesJob

    job = MeasureSeoRefreshOutcomesJob()
    assert job.name == "measure_seo_refresh_outcomes"
    assert isinstance(job.schedule, str) and job.schedule


def test_job_registered_in_core_samples():
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(getattr(j, "name", None) == "measure_seo_refresh_outcomes" for j in jobs)


class _SC:
    def __init__(self, vals):
        self._v = vals

    def get_float(self, key, default):
        return self._v.get(key, default)


@pytest.mark.asyncio
async def test_measures_due_rows_and_writes_outcome(monkeypatch):
    from services.jobs import measure_seo_refresh_outcomes as mod

    due = [
        {
            "opportunity_id": "opp-1",
            "post_id": "post-1",
            "slug": "a",
            "baseline_position": 8.0,
            "baseline_ctr": 0.001,
            "refreshed_at": None,
        }
    ]
    perf = {"impressions": 1000, "clicks": 30, "position": 5.0}
    writes = []

    class _Conn:
        async def fetch(self, sql, *args):
            return due

        async def fetchrow(self, sql, *args):
            return perf

        async def execute(self, sql, *args):
            if "UPDATE seo_opportunities" in sql:
                writes.append(args)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)

    job = mod.MeasureSeoRefreshOutcomesJob()
    res = await job.run(
        pool=_Pool(),
        config={"_site_config": _SC({"seo.refresh.outcome_measure_after_days": 14})},
    )
    assert res.ok is True
    assert res.changes_made == 1
    # outcome_position = 5.0, outcome_ctr = 30/1000 = 0.03
    assert writes and writes[0][1] == 5.0
    assert abs(writes[0][2] - 0.03) < 1e-9


@pytest.mark.asyncio
async def test_skips_post_with_no_perf_snapshot(monkeypatch):
    from services.jobs import measure_seo_refresh_outcomes as mod

    due = [{"opportunity_id": "opp-1", "post_id": "post-1", "slug": "a",
            "baseline_position": 8.0, "baseline_ctr": 0.001, "refreshed_at": None}]

    class _Conn:
        async def fetch(self, sql, *args):
            return due

        async def fetchrow(self, sql, *args):
            return None  # no snapshot

        async def execute(self, sql, *args):
            raise AssertionError("must not write outcome with no snapshot")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    monkeypatch.setattr(mod, "emit_finding", lambda **k: None)
    job = mod.MeasureSeoRefreshOutcomesJob()
    res = await job.run(pool=_Pool(), config={"_site_config": _SC({})})
    assert res.ok is True
    assert res.changes_made == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the job** `services/jobs/measure_seo_refresh_outcomes.py`:

```python
"""Scheduled job: measure the GSC outcome of an seo_refresh, N days later.

SEO Harvest Loop Phase 2c (#763). Read-only / safe-on (no master switch — it only
ever touches status='refreshed' rows; no refreshes -> no-op). For each refreshed
opportunity older than seo.refresh.outcome_measure_after_days with no outcome
yet, re-reads the latest post_performance snapshot (the LOCAL GSC mirror — no
Google API call) and records outcome_position / outcome_ctr /
outcome_measured_at, then emits a finding with the delta vs the pre-refresh
baseline. This is the empirical proof the harvest loop works.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SELECT_DUE_SQL = """
SELECT o.id AS opportunity_id, o.post_id, o.slug,
       o.baseline_position, o.baseline_ctr, o.refreshed_at
FROM seo_opportunities o
WHERE o.status = 'refreshed'
  AND o.outcome_measured_at IS NULL
  AND o.refreshed_at IS NOT NULL
  AND o.refreshed_at < NOW() - ($1::int * INTERVAL '1 day')
"""

# Latest GSC snapshot for one post (mirrors striking_distance._LATEST_SNAPSHOT_SQL).
_LATEST_PERF_SQL = """
SELECT pp.google_impressions AS impressions,
       pp.google_clicks      AS clicks,
       pp.google_avg_position AS position
FROM post_performance pp
WHERE pp.post_id = $1
ORDER BY pp.measured_at DESC
LIMIT 1
"""

_WRITE_OUTCOME_SQL = """
UPDATE seo_opportunities
   SET outcome_position    = $2,
       outcome_ctr         = $3,
       outcome_measured_at = NOW()
 WHERE id = $1::uuid
"""


def _fmt_delta(baseline: Any, outcome: float, lower_is_better: bool) -> str:
    if baseline is None:
        return "n/a"
    delta = float(baseline) - outcome if lower_is_better else outcome - float(baseline)
    arrow = "improved" if delta > 0 else ("flat" if delta == 0 else "regressed")
    return f"{float(baseline):.2f}->{outcome:.2f} ({arrow})"


class MeasureSeoRefreshOutcomesJob:
    name = "measure_seo_refresh_outcomes"
    description = (
        "Measure GSC position/CTR delta N days after an seo_refresh "
        "(read-only; reads the local post_performance mirror)"
    )
    schedule = "every 24 hours"
    idempotent = True  # outcome_measured_at guard makes it write-once

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        after_days = int(sc.get_float("seo.refresh.outcome_measure_after_days", 14)) if sc else 14

        measured: list[dict[str, Any]] = []
        try:
            async with pool.acquire() as conn:
                due = await conn.fetch(_SELECT_DUE_SQL, after_days)
                for r in due:
                    try:
                        perf = await conn.fetchrow(_LATEST_PERF_SQL, r["post_id"])
                        if perf is None:
                            continue
                        impressions = int(perf["impressions"] or 0)
                        clicks = int(perf["clicks"] or 0)
                        position = float(perf["position"]) if perf["position"] is not None else None
                        ctr = round(clicks / impressions, 5) if impressions else 0.0
                        await conn.execute(
                            _WRITE_OUTCOME_SQL, str(r["opportunity_id"]), position, ctr
                        )
                        measured.append(
                            {
                                "slug": r["slug"],
                                "position": _fmt_delta(r["baseline_position"], position or 0.0, True),
                                "ctr": _fmt_delta(r["baseline_ctr"], ctr, False),
                            }
                        )
                    except Exception as e:  # noqa: BLE001 — one bad row never aborts
                        logger.warning(
                            "[measure_seo_refresh_outcomes] failed for %s: %s", r["slug"], e
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[measure_seo_refresh_outcomes] due query failed: %s", e, exc_info=True
            )
            return JobResult(ok=False, detail=f"due query failed: {type(e).__name__}: {e}")

        if measured:
            body = "## SEO refresh — outcomes measured\n\n" + "\n".join(
                f"- **{m['slug']}** — pos {m['position']}, ctr {m['ctr']}" for m in measured
            )
            emit_finding(
                source="measure_seo_refresh_outcomes",
                kind="seo_refresh_outcome",
                title=f"SEO: {len(measured)} refresh outcome(s) measured",
                body=body,
                dedup_key=None,
                extra={"count": len(measured)},
            )

        logger.info("[measure_seo_refresh_outcomes] measured %d outcome(s)", len(measured))
        return JobResult(
            ok=True,
            detail=f"measured {len(measured)} outcome(s)",
            changes_made=len(measured),
            metrics={"measured": len(measured)},
        )
```

- [ ] **Step 4: Register the job** in `plugins/registry.py` `_SAMPLES`, immediately after the J1 row:

```python
        # SEO Harvest Loop Phase 2c — measure GSC position/CTR delta N days after
        # a refresh (read-only). Proves the loop works.
        ("jobs", "services.jobs.measure_seo_refresh_outcomes", "MeasureSeoRefreshOutcomesJob"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py -q`
Expected: PASS (all 4).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/measure_seo_refresh_outcomes.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py
git commit -F -   # "feat(seo): measure_seo_refresh_outcomes job — outcome tracking (#763)"
```

---

## Task 7: Grafana panels — refresh queue + outcomes

**Files:**

- Modify: `infrastructure/grafana/dashboards/seo-harvest.json`

The dashboard uses datasource `{ "type": "grafana-postgresql-datasource", "uid": "local-brain-db" }`. Existing panels occupy y=0..25. Add three panels at y=25+. Mirror the envelope of the existing panels (ids 1-5).

- [ ] **Step 1: Add the three panels** to the `"panels"` array (after panel id 5). Match the existing style exactly:

```json
    {
      "id": 6,
      "title": "Refresh Queue",
      "type": "stat",
      "gridPos": { "h": 5, "w": 6, "x": 0, "y": 25 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "colorMode": "background" },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "rawSql": "SELECT COUNT(*) AS value FROM seo_opportunities WHERE status = 'queued'"
        }
      ]
    },
    {
      "id": 7,
      "title": "Refreshed (lifetime)",
      "type": "stat",
      "gridPos": { "h": 5, "w": 6, "x": 6, "y": 25 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "colorMode": "background" },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "rawSql": "SELECT COUNT(*) AS value FROM seo_opportunities WHERE status = 'refreshed'"
        }
      ]
    },
    {
      "id": 8,
      "title": "Refresh Outcomes — position/CTR delta (proves the loop)",
      "type": "table",
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 30 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "options": { "showHeader": true, "sortBy": [{ "displayName": "outcome_measured_at", "desc": true }] },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "rawSql": "SELECT slug, baseline_position, outcome_position, (baseline_position - outcome_position) AS position_gain, baseline_ctr, outcome_ctr, (outcome_ctr - baseline_ctr) AS ctr_gain, refreshed_at, outcome_measured_at FROM seo_opportunities WHERE outcome_measured_at IS NOT NULL ORDER BY outcome_measured_at DESC LIMIT 50"
        }
      ]
    }
```

- [ ] **Step 2: Validate the dashboard JSON** (lint + JSON parse)

Run (from repo root): `python scripts/ci/grafana_panels_lint.py`
Expected: PASS. If the script name differs, validate JSON parses: `python -c "import json; json.load(open('infrastructure/grafana/dashboards/seo-harvest.json'))"` → no error.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/seo-harvest.json
git commit -F -   # "feat(seo): SEO Harvest dashboard — refresh queue + outcome panels (#763)"
```

---

## Task 8: Docs — update the harvest-loop status

**Files:**

- Modify: `docs/architecture/seo-harvest-loop.md`

- [ ] **Step 1: Update the Status section** at the bottom of `docs/architecture/seo-harvest-loop.md`. Replace the `- **Next:**` bullet with a shipped/next split reflecting Milestone B:

```markdown
- **Shipped (Milestone B, #763):** auto-enqueue from the analyzer
  (`enqueue_seo_refreshes`, gated on `seo.refresh.enabled`, capped by
  `seo.refresh.max_per_run`), the outcome-measurement job
  (`measure_seo_refresh_outcomes`, read-only, gated on
  `seo.refresh.outcome_measure_after_days`), the `refreshed_at` anchor + analyzer
  status-latch, and the Grafana refresh-queue + outcome-delta panels.
- **Next:** Search Console query-dimension ingestion (#764) for sharper query
  targeting, and auto-publish graduation (republish without sign-off once the
  edit-distance trust threshold is met).
```

- [ ] **Step 2: Add `seo.refresh.max_per_run` to the Settings table** in the same doc (in the Phase 2 settings table):

```markdown
| `seo.refresh.max_per_run` | `3` | Max refresh tasks auto-enqueued per run. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture/seo-harvest-loop.md
git commit -F -   # "docs(seo): Milestone B shipped — auto-enqueue + outcome tracking (#763)"
```

---

## Final verification (after all tasks)

- [ ] **Run the full SEO + jobs unit suite:**

Run: `poetry run pytest tests/unit/seo/ tests/unit/services/jobs/test_enqueue_seo_refreshes.py tests/unit/services/jobs/test_measure_seo_refresh_outcomes.py tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py -q`
Expected: all PASS.

- [ ] **Lint:** `python scripts/ci/migrations_lint.py` (PASS) and `poetry run ruff check services/jobs/enqueue_seo_refreshes.py services/jobs/measure_seo_refresh_outcomes.py services/migrations/20260612_050000_add_seo_opportunities_refreshed_at.py` (PASS).

- [ ] **Push the branch, open the PR** against `Glad-Labs/glad-labs-stack` (the source of truth — never the poindexter mirror), watch CI (`test-backend`, `migrations-smoke`, `integration-db`), merge on green.

- [ ] **Post-merge (do NOT do without explicit operator go-ahead): turning the loop on** is a one-setting flip — `seo.refresh.enabled=true`. Leave it OFF; flipping it is the operator's call. The jobs ship inert (enqueue no-ops until the flag is true; outcome job only measures already-refreshed rows).

```

```
