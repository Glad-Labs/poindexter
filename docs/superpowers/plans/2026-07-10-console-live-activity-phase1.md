# Console Live Activity — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator console a live "what is the system doing right now" pulse by adding a `live_activity` ledger that the schedulers, content pipeline, and brain write to, exposed at `GET /api/activity` and rendered in the (already-merged) console NOW RUNNING band.

**Architecture:** A single mutable-row ledger table (`live_activity`) is the source of truth. Producers call a best-effort helper (`services/live_activity.py`) at chokepoints — one edit in `PluginScheduler._runner()` captures all ~43 jobs, `template_runner`'s existing per-node callback captures content progress, and the brain's cycle wrapper captures brain liveness. One read (`get_live_activity`) powers `GET /api/activity`; the console polls it and reframes the band into **In Production / Background / Just Happened**.

**Tech Stack:** Python 3.13 · asyncpg · FastAPI · APScheduler · LangGraph · Postgres · in-browser React (console) · Node's built-in test runner (`node --test`) · pytest (`tests/integration_db` real-pool fixtures + `tests/unit`).

## Global Constraints

- **Best-effort observability:** every ledger write is wrapped so a failure logs and continues — it MUST NEVER break the job/pipeline/brain that called it. (spec: "observability; it must never become load-bearing")
- **No fabricated data:** `progress_pct` is honest — content = graph node position, never invented. Idle renders honest-empty, never a fake value (`feedback_no_dummy_data`).
- **DB-first config:** thresholds go in `app_settings` via `settings_defaults.py` (never migration files); the retention row goes in `baseline.seeds.sql` (never a migration). (`feedback_seed_data_in_baseline_not_new_migrations`)
- **Service layer is the contract:** routes stay thin adapters; no raw SQL in `routes/`. (adapter-purity)
- **Fail loud on required config; swallow only observability writes.** These are not in tension: settings reads use documented defaults; only the ledger writes swallow.
- **Migration interface:** `async def up(pool) -> None` in `services/migrations/YYYYMMDD_HHMMSS_<slug>.py`.
- **Console modules that are unit-tested must be dual-mode** (`window.X` global + `module.exports`) like `js/kpis.js`, so `node --test` can `require()` them.
- **Branch:** all work on a branch off `origin/main` (the #2260 band is already merged to main); every task commits; PR at the end.

---

### Task 1: `live_activity` schema, retention seed, and settings defaults

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260710_140000_create_live_activity.py`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (append one `retention_policies` row)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add 3 keys)
- Test: `src/cofounder_agent/tests/integration_db/test_live_activity_schema.py`

**Interfaces:**

- Produces: table `live_activity(id BIGSERIAL, kind TEXT, ref_id TEXT, title TEXT, status TEXT, step TEXT, progress_pct SMALLINT, detail JSONB, started_at TIMESTAMPTZ, updated_at TIMESTAMPTZ, finished_at TIMESTAMPTZ)`; settings `live_activity_freshness_seconds`, `live_activity_reaper_seconds`, `live_activity_recent_limit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration_db/test_live_activity_schema.py
"""live_activity table exists with the expected columns + indexes after migrations."""
import pytest

pytestmark = pytest.mark.asyncio

async def test_live_activity_columns(test_pool):  # test_pool: session pool with migrations applied
    async with test_pool.acquire() as conn:
        cols = {r["column_name"]: r["data_type"] for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'live_activity'"
        )}
    assert cols["kind"] == "text"
    assert cols["progress_pct"] == "smallint"
    assert cols["finished_at"] == "timestamp with time zone"
    assert "detail" in cols  # jsonb

async def test_live_activity_running_index(test_pool):
    async with test_pool.acquire() as conn:
        idx = [r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'live_activity'"
        )]
    assert any("running" in n for n in idx)
```

> NOTE: `test_pool` is the shared migrations-applied session pool defined in `tests/integration_db/conftest.py` (it depends on `fixtures_loaded`, so the schema is present). Use `test_pool` — NOT the auto-rollback `test_txn` — because the `live_activity` helpers acquire their own connections, so a single-connection rollback txn wouldn't see their writes. Same choice `test_atom_runs_incremental.py` makes.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_live_activity_schema.py -v`
Expected: FAIL — relation "live_activity" does not exist.

- [ ] **Step 3: Write the migration**

```python
# services/migrations/20260710_140000_create_live_activity.py
"""Create the live_activity ledger — mutable in-flight rows carrying live
progress + heartbeat across every activity kind (jobs / content / media / brain).
The console 'what is the system doing now' pulse reads from this table.
stdlib-only so migrations-smoke applies it without a full app boot."""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS live_activity (
                id            BIGSERIAL PRIMARY KEY,
                kind          TEXT NOT NULL,
                ref_id        TEXT,
                title         TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'running',
                step          TEXT,
                progress_pct  SMALLINT,
                detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
                started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at   TIMESTAMPTZ
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_activity_running "
            "ON live_activity (started_at DESC) WHERE finished_at IS NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_activity_recent "
            "ON live_activity (finished_at DESC) WHERE finished_at IS NOT NULL"
        )
    logger.info("create_live_activity up: live_activity ledger + partial indexes ready")
```

- [ ] **Step 4: Add the retention seed + settings defaults**

Append to `services/migrations/0000_baseline.seeds.sql` (idempotent; ttl_prune only touches finished rows because `finished_at` is NULL on live rows):

```sql
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata) VALUES ('a1b2c3d4-0002-4000-8000-000000000024', 'live_activity', 'ttl_prune', 'live_activity', NULL, 'finished_at', 2, NULL, NULL, true, '{}'::jsonb, '{"description": "Console live-activity ledger — prune finished rows after 2d; running rows (finished_at NULL) are never pruned"}'::jsonb) ON CONFLICT (id) DO NOTHING;
```

Add to the `DEFAULTS` dict in `services/settings_defaults.py`:

```python
    "live_activity_freshness_seconds": "120",   # a running row counts as live only if updated_at is within this window
    "live_activity_reaper_seconds": "300",      # running rows with no heartbeat past this get marked 'stale'
    "live_activity_recent_limit": "20",         # size of the "recent trail"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_live_activity_schema.py -v && python scripts/ci/migrations_lint.py`
Expected: PASS; lint clean.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260710_140000_create_live_activity.py \
        src/cofounder_agent/services/migrations/0000_baseline.seeds.sql \
        src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/integration_db/test_live_activity_schema.py
git commit -m "feat(activity): live_activity ledger table + retention + settings"
```

---

### Task 2: `live_activity.py` write helper (`begin` / `update` / `finish`)

**Files:**

- Create: `src/cofounder_agent/services/live_activity.py`
- Test: `src/cofounder_agent/tests/integration_db/test_live_activity_helper.py`
- Test: `src/cofounder_agent/tests/unit/services/test_live_activity_swallow.py`

**Interfaces:**

- Consumes: a pool (asyncpg) — passed by scheduler/`template_runner`/brain (all hold their own pool).
- Produces:
  - `async def begin(pool, *, kind: str, ref_id: str | None, title: str, detail: dict | None = None) -> int | None`
  - `async def update(pool, activity_id: int | None, *, step: str | None = None, pct: int | None = None) -> None`
  - `async def finish(pool, activity_id: int | None, *, status: str = "ok") -> None`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration_db/test_live_activity_helper.py
import pytest
from services import live_activity

pytestmark = pytest.mark.asyncio

async def test_begin_update_finish_roundtrip(test_pool):
    aid = await live_activity.begin(test_pool, kind="job", ref_id="demo_job", title="Demo job")
    assert isinstance(aid, int)
    async with test_pool.acquire() as c:
        row = await c.fetchrow("SELECT status, finished_at FROM live_activity WHERE id=$1", aid)
    assert row["status"] == "running" and row["finished_at"] is None

    await live_activity.update(test_pool, aid, step="qa.critic", pct=62)
    await live_activity.finish(test_pool, aid, status="ok")
    async with test_pool.acquire() as c:
        row = await c.fetchrow("SELECT step, progress_pct, status, finished_at FROM live_activity WHERE id=$1", aid)
    assert row["step"] == "qa.critic" and row["progress_pct"] == 62
    assert row["status"] == "ok" and row["finished_at"] is not None

async def test_update_finish_noop_on_none_id(test_pool):
    # A failed begin() returns None; update/finish must silently no-op.
    await live_activity.update(test_pool, None, step="x")
    await live_activity.finish(test_pool, None)
```

- [ ] **Step 2: Write the failing unit test (swallow-on-error)**

```python
# tests/unit/services/test_live_activity_swallow.py
import pytest
from services import live_activity

pytestmark = pytest.mark.asyncio

class _BoomPool:
    def acquire(self):  # not even an async ctx — any use raises
        raise RuntimeError("db down")

async def test_begin_swallows_and_returns_none():
    assert await live_activity.begin(_BoomPool(), kind="job", ref_id="j", title="t") is None

async def test_update_finish_swallow():
    await live_activity.update(_BoomPool(), 1, step="s")   # must not raise
    await live_activity.finish(_BoomPool(), 1)             # must not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_swallow.py -v`
Expected: FAIL — `No module named 'services.live_activity'`.

- [ ] **Step 4: Write the helper**

```python
# services/live_activity.py
"""Best-effort live-activity ledger writes. EVERY function swallows its own
errors (logs, never raises) — this is observability and must never break the
job / pipeline / brain that calls it. Pool-based so the worker AND the
minimal-dependency brain daemon can both use it (spec: the ledger seam)."""
from __future__ import annotations
import json, logging
from typing import Any
logger = logging.getLogger(__name__)

async def begin(pool: Any, *, kind: str, ref_id: str | None, title: str,
                detail: dict | None = None) -> int | None:
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO live_activity (kind, ref_id, title, detail) "
                "VALUES ($1, $2, $3, $4::jsonb) RETURNING id",
                kind, ref_id, title, json.dumps(detail or {}),
            )
    except Exception as exc:  # noqa: BLE001 — never break the caller
        logger.debug("live_activity.begin swallowed: %s", exc)
        return None

async def update(pool: Any, activity_id: int | None, *,
                 step: str | None = None, pct: int | None = None) -> None:
    if activity_id is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE live_activity SET step = COALESCE($2, step), "
                "progress_pct = COALESCE($3, progress_pct), updated_at = now() "
                "WHERE id = $1 AND finished_at IS NULL",
                activity_id, step, pct,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_activity.update swallowed: %s", exc)

async def finish(pool: Any, activity_id: int | None, *, status: str = "ok") -> None:
    if activity_id is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE live_activity SET status = $2, updated_at = now(), "
                "finished_at = now() WHERE id = $1 AND finished_at IS NULL",
                activity_id, status,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_activity.finish swallowed: %s", exc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_swallow.py tests/integration_db/test_live_activity_helper.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/live_activity.py \
        src/cofounder_agent/tests/unit/services/test_live_activity_swallow.py \
        src/cofounder_agent/tests/integration_db/test_live_activity_helper.py
git commit -m "feat(activity): best-effort begin/update/finish ledger writes"
```

---

### Task 3: `get_live_activity` read + `reap_stale`

**Files:**

- Modify: `src/cofounder_agent/services/live_activity.py` (add two functions)
- Test: `src/cofounder_agent/tests/integration_db/test_live_activity_read.py`

**Interfaces:**

- Produces:
  - `async def get_live_activity(pool, *, freshness_seconds: int, recent_limit: int) -> dict` → `{"running": [...], "recent": [...], "summary": {"running_by_kind": {...}}}`
  - `async def reap_stale(pool, *, reaper_seconds: int) -> int` → count marked `stale`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration_db/test_live_activity_read.py
import pytest
from services import live_activity

pytestmark = pytest.mark.asyncio

async def test_running_excludes_stale_and_finished(test_pool):
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM live_activity")
        # fresh running (shows), stale running (hidden by window), finished (in recent)
        await c.execute("INSERT INTO live_activity (kind, ref_id, title) VALUES ('content','1','Fresh')")
        await c.execute("INSERT INTO live_activity (kind, ref_id, title, updated_at) "
                        "VALUES ('job','2','Stale', now() - interval '10 minutes')")
        await c.execute("INSERT INTO live_activity (kind, ref_id, title, status, finished_at) "
                        "VALUES ('job','3','Done','ok', now())")
    out = await live_activity.get_live_activity(test_pool, freshness_seconds=120, recent_limit=20)
    running_titles = {r["title"] for r in out["running"]}
    assert running_titles == {"Fresh"}
    assert any(r["title"] == "Done" for r in out["recent"])
    assert out["summary"]["running_by_kind"].get("content") == 1

async def test_reap_marks_stale(test_pool):
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM live_activity")
        await c.execute("INSERT INTO live_activity (kind, ref_id, title, updated_at) "
                        "VALUES ('job','9','Orphan', now() - interval '10 minutes')")
    n = await live_activity.reap_stale(test_pool, reaper_seconds=300)
    assert n == 1
    async with test_pool.acquire() as c:
        row = await c.fetchrow("SELECT status, finished_at FROM live_activity WHERE ref_id='9'")
    assert row["status"] == "stale" and row["finished_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_live_activity_read.py -v`
Expected: FAIL — `module 'services.live_activity' has no attribute 'get_live_activity'`.

- [ ] **Step 3: Add the read + reaper**

```python
# append to services/live_activity.py
async def get_live_activity(pool: Any, *, freshness_seconds: int, recent_limit: int) -> dict:
    try:
        async with pool.acquire() as conn:
            running = await conn.fetch(
                "SELECT kind, ref_id, title, status, step, progress_pct, detail, "
                "  started_at, updated_at "
                "FROM live_activity "
                "WHERE finished_at IS NULL "
                "  AND updated_at > now() - ($1 || ' seconds')::interval "
                "ORDER BY (kind IN ('content','media')) DESC, started_at ASC",
                str(freshness_seconds),
            )
            recent = await conn.fetch(
                "SELECT kind, ref_id, title, status, started_at, finished_at, "
                "  EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000 AS duration_ms "
                "FROM live_activity WHERE finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT $1",
                recent_limit,
            )
        summary: dict[str, int] = {}
        for r in running:
            summary[r["kind"]] = summary.get(r["kind"], 0) + 1
        return {
            "running": [dict(r) for r in running],
            "recent": [dict(r) for r in recent],
            "summary": {"running_by_kind": summary},
        }
    except Exception as exc:  # noqa: BLE001 — the read is advisory; empty beats a 500
        logger.warning("live_activity.get_live_activity failed: %s", exc)
        return {"running": [], "recent": [], "summary": {"running_by_kind": {}}}

async def reap_stale(pool: Any, *, reaper_seconds: int) -> int:
    try:
        async with pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE live_activity SET status = 'stale', finished_at = now() "
                "WHERE finished_at IS NULL "
                "  AND updated_at < now() - ($1 || ' seconds')::interval",
                str(reaper_seconds),
            )
        return int(str(res).split()[-1]) if str(res).startswith("UPDATE") else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_activity.reap_stale swallowed: %s", exc)
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_live_activity_read.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/live_activity.py src/cofounder_agent/tests/integration_db/test_live_activity_read.py
git commit -m "feat(activity): get_live_activity read + reap_stale (freshness window)"
```

---

### Task 4: `GET /api/activity` route

**Files:**

- Create: `src/cofounder_agent/routes/activity_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (register the router)
- Test: `src/cofounder_agent/tests/integration_db/test_activity_route.py`

**Interfaces:**

- Consumes: `get_live_activity` (Task 3); `verify_api_token`, `get_database_dependency`, `get_site_config_dependency` (existing).
- Produces: `GET /api/activity` → the `get_live_activity` dict.

- [ ] **Step 1: Write the failing test** (mirror `tests/integration_db/` route tests that build a `TestClient` with a real pool + a minted token; copy that harness from `test_findings_route` if present, else drive the service directly)

```python
# tests/integration_db/test_activity_route.py
import pytest
from services import live_activity

pytestmark = pytest.mark.asyncio

async def test_activity_shape(test_pool):
    # The route is a thin adapter; assert it returns the service contract keys.
    from routes.activity_routes import _read_activity  # thin internal used by the handler
    out = await _read_activity(test_pool, site_config=_FakeConfig())
    assert set(out.keys()) == {"running", "recent", "summary"}

class _FakeConfig:
    def get(self, k, d=None): return {"live_activity_freshness_seconds": "120",
        "live_activity_recent_limit": "20"}.get(k, d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_activity_route.py -v`
Expected: FAIL — no module `routes.activity_routes`.

- [ ] **Step 3: Write the route** (thin adapter; reads thresholds from `site_config`, delegates to the service)

```python
# routes/activity_routes.py
"""GET /api/activity — the console live-activity pulse (running + recent trail)."""
from fastapi import APIRouter, Depends
from services.database_service import DatabaseService
from services.live_activity import get_live_activity
from services.site_config import SiteConfig
from utils.dependencies import get_database_dependency, get_site_config_dependency
from routes.auth_middleware import verify_api_token  # match the import used by findings_routes

router = APIRouter(prefix="/api", tags=["activity"], dependencies=[Depends(verify_api_token)])

async def _read_activity(pool, *, site_config: SiteConfig) -> dict:
    return await get_live_activity(
        pool,
        freshness_seconds=int(site_config.get("live_activity_freshness_seconds", "120")),
        recent_limit=int(site_config.get("live_activity_recent_limit", "20")),
    )

@router.get("/activity", summary="Live system activity (running + recent)")
async def activity(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict:
    return await _read_activity(db_service.pool, site_config=site_config)
```

> NOTE for implementer: confirm the exact import paths for `verify_api_token`, `get_database_dependency`, `get_site_config_dependency`, and `db_service.pool` against `routes/findings_routes.py` — copy whatever that file uses verbatim.

- [ ] **Step 4: Register the router.** In `utils/route_registration.py`, add to the route-module list (next to the other operator reads):

```python
    ("routes.activity_routes", "router", "activity_router", "live-activity pulse (GET /api/activity)"),
```

- [ ] **Step 5: Run test + adapter-purity lint**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_activity_route.py -v && python scripts/ci/adapter_purity_lint.py`
Expected: PASS; no inline-SQL violation (the route has none).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/routes/activity_routes.py src/cofounder_agent/utils/route_registration.py src/cofounder_agent/tests/integration_db/test_activity_route.py
git commit -m "feat(activity): GET /api/activity route"
```

---

### Task 5: Scheduler seam — all background jobs (liveness)

**Files:**

- Modify: `src/cofounder_agent/plugins/scheduler.py` (`_runner`, ~line 196–215)
- Test: `src/cofounder_agent/tests/unit/plugins/test_scheduler_activity.py`

**Interfaces:**

- Consumes: `live_activity.begin/finish` (Task 2).
- Produces: a `job` ledger row per job run (unless the job sets `activity_silent = True`).

- [ ] **Step 1: Write the failing test** (monkeypatch the helper; assert begin+finish bracket `job.run`)

```python
# tests/unit/plugins/test_scheduler_activity.py
import pytest
from plugins.scheduler import PluginScheduler
from plugins.job import JobResult

pytestmark = pytest.mark.asyncio

class _Job:
    name = "demo"; description = "Demo"; schedule = "every 5 minutes"; idempotent = True
    async def run(self, pool, cfg): return JobResult(ok=True, detail="ok", changes_made=0)

async def test_job_run_brackets_activity(monkeypatch):
    calls = []
    async def fake_begin(pool, **kw): calls.append(("begin", kw)); return 7
    async def fake_finish(pool, aid, **kw): calls.append(("finish", aid, kw))
    monkeypatch.setattr("plugins.scheduler.live_activity.begin", fake_begin)
    monkeypatch.setattr("plugins.scheduler.live_activity.finish", fake_finish)
    await PluginScheduler._invoke_job_with_activity(pool=None, job=_Job(), cfg={})
    assert calls[0][0] == "begin" and calls[0][1]["kind"] == "job"
    assert calls[-1][0] == "finish" and calls[-1][1] == 7 and calls[-1][2]["status"] == "ok"

async def test_activity_silent_job_skips(monkeypatch):
    calls = []
    monkeypatch.setattr("plugins.scheduler.live_activity.begin",
                        lambda *a, **k: calls.append("begin"))
    job = _Job(); job.activity_silent = True
    await PluginScheduler._invoke_job_with_activity(pool=None, job=job, cfg={})
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_activity.py -v`
Expected: FAIL — `_invoke_job_with_activity` not defined.

- [ ] **Step 3: Extract the seam into a testable static method and call it from `_runner`**

Add `from services import live_activity` at the top of `plugins/scheduler.py`. Add the static method to `PluginScheduler`:

```python
    @staticmethod
    async def _invoke_job_with_activity(*, pool, job, cfg) -> "JobResult":
        """Run a job, bracketed by a best-effort live_activity row (unless the
        job opts out via `activity_silent = True`). The ledger writes never
        affect the job's own result/exception path."""
        aid = None
        if not getattr(job, "activity_silent", False):
            aid = await live_activity.begin(
                pool, kind="job", ref_id=job.name,
                title=getattr(job, "description", None) or job.name,
            )
        try:
            result = await job.run(pool, cfg)
            await live_activity.finish(pool, aid, status="ok" if result.ok else "fail")
            return result
        except Exception:
            await live_activity.finish(pool, aid, status="fail")
            raise
```

Then in `_runner`, replace the bare `result = await job.run(self._pool, live_cfg.config)` with:

```python
                result = await PluginScheduler._invoke_job_with_activity(
                    pool=self._pool, job=job, cfg=live_cfg.config,
                )
```

(Leave the surrounding logging / `_escalate_job_failure` / `_record_last_run` exactly as-is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_activity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/plugins/scheduler.py src/cofounder_agent/tests/unit/plugins/test_scheduler_activity.py
git commit -m "feat(activity): scheduler seam — job liveness rows"
```

---

### Task 6: `template_runner` seam — content progress

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` (in `run()`, around the `records.set_on_record(...)` block ~line 1345–1360, and around `ainvoke` ~1454)
- Create: `src/cofounder_agent/services/live_activity_content.py` (pure helper: node → (step, pct))
- Test: `src/cofounder_agent/tests/unit/services/test_live_activity_content.py`

**Interfaces:**

- Consumes: `live_activity.begin/update/finish`; the existing `_persist_record(seq, rec)` callback.
- Produces: `def content_step_pct(rec, seq: int, total: int) -> tuple[str | None, int | None]` (pure).

- [ ] **Step 1: Write the failing test** (test the pure mapper — the risky logic — not the whole LangGraph run)

```python
# tests/unit/services/test_live_activity_content.py
from services.live_activity_content import content_step_pct

class _Rec:
    def __init__(self, name): self.name = name   # mirrors TemplateRunRecord.name

def test_step_is_node_name_and_pct_is_position():
    step, pct = content_step_pct(_Rec("qa.critic"), seq=25, total=42)
    assert step == "qa.critic"
    assert pct == round(100 * 26 / 42)   # 1-based node position

def test_pct_capped_and_total_guarded():
    assert content_step_pct(_Rec("x"), seq=0, total=0)[1] is None   # no total → step-only
    assert content_step_pct(_Rec("x"), seq=99, total=42)[1] == 99   # never > 99 until finish
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_content.py -v`
Expected: FAIL — no module `services.live_activity_content`.

- [ ] **Step 3: Write the pure mapper**

```python
# services/live_activity_content.py
"""Pure mapping from a template node record → (step label, honest progress %).
pct is NODE POSITION (node_index / total), never time — documented in the UI."""
from __future__ import annotations
from typing import Any

def content_step_pct(rec: Any, seq: int, total: int) -> tuple[str | None, int | None]:
    step = getattr(rec, "name", None) or getattr(rec, "node", None)   # TemplateRunRecord.name is the node label
    if not total or total <= 0:
        return step, None
    pct = round(100 * (seq + 1) / total)
    return step, min(99, max(1, pct))   # 1..99 while running; finish() flips to done
```

- [ ] **Step 4: Wire it into `template_runner.run()`.** Add the imports at the top of the file:

```python
from services import live_activity
from services.live_activity_content import content_step_pct
```

**(a) Open the row** immediately after the `thread_id = (...)` assignment (~line 1333, just before the `if isinstance(records, _RecordingSink):` block). `graph` is the LangGraph `StateGraph`, in scope from `assert graph is not None` (line 1327); `graph.nodes` is its node dict, so `len(graph.nodes)` is the honest total:

```python
        # ── live-activity: content-in-production row (best-effort) ──
        _content_total = len(getattr(graph, "nodes", {}) or {})
        _content_aid = await live_activity.begin(
            self._pool, kind="content",
            ref_id=str(initial_state.get("task_id") or thread_id),
            title=str(initial_state.get("topic") or initial_state.get("task_name")
                      or f"task {initial_state.get('task_id')}"),
            detail={"template": template_slug},
        )
```

**(b) Emit progress per node** — inside the existing `_persist_record(seq, rec)` closure (~line 1345), right after the `await persist_one_atom_run(...)` call:

```python
                step, pct = content_step_pct(rec, seq, _content_total)
                await live_activity.update(self._pool, _content_aid, step=step, pct=pct)
```

(`_content_aid` is captured from the enclosing `run()` scope — assigned in (a) before `ainvoke` fires the callback.)

**(c) Close on the exception path** — inside the `except Exception as exc:` wrapping `ainvoke` (~line 1455), before `return await self._handle_run_exception(...)`:

```python
                except Exception as exc:
                    await live_activity.finish(self._pool, _content_aid, status="fail")
                    return await self._handle_run_exception(
                        exc, template_slug, initial_state, records, on_event=on_event,
                    )
```

**(d) Close on the success/halt path** — right after `ok = not any(r.halted for r in records)` (~line 1467):

```python
        await live_activity.finish(self._pool, _content_aid, status="ok" if ok else "fail")
```

> NOTE: `_content_aid` must be visible to the `_persist_record` closure AND both finish sites — assigning it in (a) at function scope (before the `if isinstance(records, _RecordingSink):` block) satisfies all three. If `records` isn't a `_RecordingSink` (no per-node callback), the row still gets begin+finish liveness — just no intermediate `pct`, which is honest rather than fabricated.

- [ ] **Step 5: Run the pure-mapper test + the template_runner suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_content.py tests/unit -k template_runner -v`
Expected: PASS (no regressions in existing template_runner tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/live_activity_content.py \
        src/cofounder_agent/services/template_runner.py \
        src/cofounder_agent/tests/unit/services/test_live_activity_content.py
git commit -m "feat(activity): template_runner seam — content progress rows"
```

---

### Task 7: Brain cycle seam (liveness)

**Files:**

- Modify: `src/cofounder_agent/brain/brain_daemon.py` (`_run_cycle_with_watchdog`, ~line 637)
- Test: `src/cofounder_agent/tests/unit/brain/test_brain_activity.py`

**Interfaces:**

- Consumes: `live_activity.begin/finish` (Task 2; imported into the brain — it's asyncpg-only, which the brain has).

- [ ] **Step 1: Write the failing test** (monkeypatch; assert the cycle is bracketed)

```python
# tests/unit/brain/test_brain_activity.py
import pytest
import brain.brain_daemon as bd

pytestmark = pytest.mark.asyncio

async def test_cycle_brackets_activity(monkeypatch):
    calls = []
    async def fake_begin(pool, **kw): calls.append(("begin", kw["kind"])); return 3
    async def fake_finish(pool, aid, **kw): calls.append(("finish", aid, kw["status"]))
    monkeypatch.setattr(bd.live_activity, "begin", fake_begin)
    monkeypatch.setattr(bd.live_activity, "finish", fake_finish)
    async def ok_cycle(pool): return None
    await bd._run_cycle_with_watchdog(pool=None, cycle_timeout=5, run_cycle_fn=ok_cycle)
    assert calls == [("begin", "brain"), ("finish", 3, "ok")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_brain_activity.py -v`
Expected: FAIL — `bd.live_activity` doesn't exist / cycle not bracketed.

- [ ] **Step 3: Bracket the cycle.** Add `from services import live_activity` near the top of `brain/brain_daemon.py`. In `_run_cycle_with_watchdog`, wrap the `asyncio.wait_for(fn(pool), ...)` call:

```python
    aid = await live_activity.begin(pool, kind="brain", ref_id="monitor_cycle", title="Brain monitor cycle")
    try:
        result = await asyncio.wait_for(fn(pool), timeout=cycle_timeout)
        await live_activity.finish(pool, aid, status="ok")
        return result
    except Exception:
        await live_activity.finish(pool, aid, status="fail")
        raise
```

> NOTE: keep the existing watchdog semantics (the `asyncio.wait_for` + timeout handling) — only add the begin/finish bracket. If the function already has a try/except for the timeout, thread the finish() calls into the existing branches rather than double-wrapping.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_brain_activity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/brain/brain_daemon.py src/cofounder_agent/tests/unit/brain/test_brain_activity.py
git commit -m "feat(activity): brain cycle liveness rows"
```

---

### Task 8: Stale-activity reaper job

**Files:**

- Create: `src/cofounder_agent/services/jobs/reap_stale_activity.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (register the job in `_SAMPLES`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_reap_stale_activity.py`

**Interfaces:**

- Consumes: `live_activity.reap_stale` (Task 3); `site_config.get`.
- Produces: `ReapStaleActivityJob` (name `reap_stale_activity`, `activity_silent = True`, every 1 minute).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/jobs/test_reap_stale_activity.py
import pytest
from services.jobs.reap_stale_activity import ReapStaleActivityJob

pytestmark = pytest.mark.asyncio

class _Cfg:
    def get(self, k, d=None): return {"live_activity_reaper_seconds": "300"}.get(k, d)

async def test_reaper_is_silent_and_calls_reap(monkeypatch):
    seen = {}
    async def fake_reap(pool, *, reaper_seconds): seen["s"] = reaper_seconds; return 2
    monkeypatch.setattr("services.jobs.reap_stale_activity.reap_stale", fake_reap)
    job = ReapStaleActivityJob()
    assert job.activity_silent is True   # the reaper must not log itself every minute
    res = await job.run(pool=None, config={"_site_config": _Cfg()})
    assert res.ok and res.changes_made == 2 and seen["s"] == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_reap_stale_activity.py -v`
Expected: FAIL — no module.

- [ ] **Step 3: Write the job** (model the class shape on `services/jobs/dispatch_media_pipeline.py`)

```python
# services/jobs/reap_stale_activity.py
"""Mark live_activity rows whose heartbeat has gone stale as 'stale' so orphaned
'running' rows (worker died mid-job) don't show as running forever and don't
accumulate. Complements the read's freshness-window filter."""
from __future__ import annotations
from typing import Any
from plugins.job import JobResult
from services.live_activity import reap_stale

class ReapStaleActivityJob:
    name = "reap_stale_activity"
    description = "Mark stale live_activity running rows (heartbeat lapsed)"
    schedule = "every 1 minute"
    idempotent = True
    activity_silent = True   # don't write a ledger row for the reaper itself

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=True, detail="no pool", changes_made=0)
        sc = config.get("_site_config")
        secs = int(sc.get("live_activity_reaper_seconds", "300")) if sc else 300
        n = await reap_stale(pool, reaper_seconds=secs)
        return JobResult(ok=True, detail=f"reaped {n}", changes_made=n)

__all__ = ["ReapStaleActivityJob"]
```

- [ ] **Step 4: Register the job.** In `plugins/registry.py`, import `ReapStaleActivityJob` and add it to the `_SAMPLES` registration list next to the other maintenance jobs (match the exact registration idiom used there).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_reap_stale_activity.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/reap_stale_activity.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/unit/services/jobs/test_reap_stale_activity.py
git commit -m "feat(activity): stale-row reaper job"
```

---

### Task 9: Console adapter — `PX.api.activity()`, mock seed, pure mapper

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (add `activity()` to `PX.api`)
- Modify: `src/cofounder_agent/console/js/data.js` (add `PX.activity` mock seed)
- Create: `src/cofounder_agent/console/js/activity.js` (dual-mode pure mapper)
- Modify: `src/cofounder_agent/console/js/app.jsx` (poll resource)
- Test: `src/cofounder_agent/console/js/__tests__/activity.test.js`

**Interfaces:**

- Produces: `PX.api.activity()` → `{running, recent, summary}`; `PX.activity` (mock); `mapActivity(raw, nowMs)` → `{ inProduction: [...], background: [...], trail: [...], summary }` (dual-mode export).

- [ ] **Step 1: Write the failing test** (pure mapper — the branchy bit: splitting kinds, elapsed, honest-empty)

```javascript
// console/js/__tests__/activity.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { mapActivity } = require('../activity.js');

const NOW = Date.parse('2026-07-10T12:00:00Z');
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

test('splits content/media into production, jobs+brain into background', () => {
  const raw = {
    running: [
      {
        kind: 'content',
        title: 'Post',
        step: 'qa.critic',
        progress_pct: 62,
        started_at: iso(180000),
      },
      {
        kind: 'job',
        ref_id: 'dispatch_media_pipeline',
        title: 'Media dispatch',
        started_at: iso(60000),
      },
      { kind: 'brain', title: 'Brain monitor cycle', started_at: iso(5000) },
    ],
    recent: [],
    summary: { running_by_kind: { content: 1, job: 1, brain: 1 } },
  };
  const m = mapActivity(raw, NOW);
  assert.equal(m.inProduction.length, 1);
  assert.equal(m.background.length, 2);
  assert.equal(m.inProduction[0].elapsedS, 180); // seconds since started_at
});

test('honest-empty when nothing running', () => {
  const m = mapActivity(
    { running: [], recent: [], summary: { running_by_kind: {} } },
    NOW
  );
  assert.deepEqual(m.inProduction, []);
  assert.deepEqual(m.background, []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/activity.test.js`
Expected: FAIL — cannot find `../activity.js`.

- [ ] **Step 3: Write the dual-mode mapper** (mirror the `js/kpis.js` dual-mode footer)

```javascript
// console/js/activity.js
'use strict';
(function (root) {
  function elapsedS(startIso, nowMs) {
    const t = Date.parse(startIso);
    return Number.isFinite(t)
      ? Math.max(0, Math.round((nowMs - t) / 1000))
      : null;
  }
  function mapActivity(raw, nowMs) {
    const running = (raw && raw.running) || [];
    const recent = (raw && raw.recent) || [];
    const decorate = (r) => ({ ...r, elapsedS: elapsedS(r.started_at, nowMs) });
    return {
      inProduction: running
        .filter((r) => r.kind === 'content' || r.kind === 'media')
        .map(decorate),
      background: running
        .filter((r) => r.kind === 'job' || r.kind === 'brain')
        .map(decorate),
      trail: recent.map((r) => ({ ...r, durationMs: r.duration_ms })),
      summary: (raw && raw.summary) || { running_by_kind: {} },
    };
  }
  const api = { mapActivity };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.PXActivity = api; // browser global for the band
})(typeof window !== 'undefined' ? window : globalThis);
```

- [ ] **Step 4: Add `PX.api.activity()`** in `js/api.js` (using the existing `pick` + `http` helpers):

```javascript
    activity() {
      return pick(
        async () => {
          const r = await http('GET', '/api/activity');
          return r || { running: [], recent: [], summary: { running_by_kind: {} } };
        },
        () => PX.activity
      );
    },
```

- [ ] **Step 5: Add the `PX.activity` mock seed** in `js/data.js` (register on the `window.PX = {...}` object, like `mediaRendering`):

```javascript
const activity = {
  running: [
    {
      kind: 'content',
      ref_id: '4412',
      title: 'A Field Guide to pgvector at Small Scale',
      status: 'running',
      step: 'qa.critic',
      progress_pct: 62,
      started_at: isoAgo(3 * 60),
      detail: { model: 'gemma-4-31b' },
    },
    {
      kind: 'job',
      ref_id: 'dispatch_media_pipeline',
      title: 'Media dispatch',
      status: 'running',
      started_at: isoAgo(3 * 60),
    },
    {
      kind: 'brain',
      ref_id: 'monitor_cycle',
      title: 'Brain monitor cycle',
      status: 'running',
      started_at: isoAgo(5),
    },
  ],
  recent: [
    {
      kind: 'job',
      title: 'topic-harvest',
      status: 'ok',
      started_at: isoAgo(48),
      finished_at: isoAgo(40),
      duration_ms: 8200,
    },
    {
      kind: 'job',
      title: 'render_prometheus_rules',
      status: 'ok',
      started_at: isoAgo(122),
      finished_at: isoAgo(121),
      duration_ms: 1100,
    },
    {
      kind: 'job',
      title: 'deepeval_g_eval',
      status: 'fail',
      started_at: isoAgo(430),
      finished_at: isoAgo(427),
      duration_ms: 3400,
    },
  ],
  summary: { running_by_kind: { content: 1, job: 1, brain: 1, media: 0 } },
};
```

Add an `isoAgo` helper near the other mock time helpers if absent: `const isoAgo = (s) => new Date(PX.now.getTime() - s * 1000).toISOString();` and add `activity,` to the `window.PX = { ... }` literal.

- [ ] **Step 6: Add the poll resource** in `js/app.jsx` (next to the other `usePolledResource` calls, ~line 187):

```javascript
const activityR = window.PXR.usePolledResource(
  () => PX.api.activity(),
  { intervalMs: 3000, key: 'activity' } // ~3s pulse; Task 10 consumes activityR.data
);
const activity = activityR.data || PX.activity;
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm run test:console 2>&1 | tail -5`
Expected: the new `activity.test.js` cases pass; existing console tests still green.

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/console/js/activity.js src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/data.js src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/__tests__/activity.test.js
git commit -m "feat(activity): console PX.api.activity() + mock seed + mapper"
```

---

### Task 10: Reframe the NOW RUNNING band to the live pulse

**Files:**

- Modify: `src/cofounder_agent/console/js/nowrunning.jsx` (consume `activity` not `pipeline`/`PX.mediaRendering`)
- Modify: `src/cofounder_agent/console/js/app.jsx` (band call site ~line 1420: pass `activity`, drop the old props)
- Modify: `src/cofounder_agent/console/css/console.css` (add a `.nowrun__trow` trail row style if not already present — reuse the mockup's classes)

**Interfaces:**

- Consumes: `mapActivity` (Task 9, via `window.PXActivity.mapActivity`) + `activity` from `app.jsx`.
- Produces: the band renders three columns — **In Production** (`inProduction`), **Background** (`background`), **Just Happened** (`trail`).

- [ ] **Step 1: Update the call site.** In `app.jsx`, replace the current `<NowRunningBand .../>` props with:

```javascript
<NowRunningBand activity={activity} onOpenTask={(t) => open('task', t)} />
```

- [ ] **Step 2: Rewrite `NowRunningBand`** to consume the activity shape. Column 1 = `inProduction` (content/media rows with `progress_pct` bars, amber for `kind==='media'`), Column 2 = `background` (job/brain rows with `elapsedS`), Column 3 = `trail` (recent with `durationMs` + ✓/✕/⚠ by status). Header summary from `activity.summary`. Full replacement body:

```jsx
function NowRunningBand({ activity, onOpenTask }) {
  const m = window.PXActivity.mapActivity(activity || {}, Date.now());
  const fmtAge = (s) =>
    s == null ? '' : s < 90 ? `${s}s` : `${Math.round(s / 60)}m`;
  const glyph = { ok: '✓', fail: '✕', stale: '⚠' };
  return (
    <section className="nowrun" aria-label="System pulse">
      <header className="nowrun__head">
        <span className="nowrun__dot" />
        <span className="nowrun__title">SYSTEM PULSE</span>
        <span className="nowrun__sum">
          {m.inProduction.length} in production · {m.background.length}{' '}
          background · {m.trail.length} recent
        </span>
      </header>
      <div className="nowrun__grid">
        <div className="nowrun__col">
          <div className="nowrun__collabel">IN PRODUCTION</div>
          {m.inProduction.length === 0 && (
            <div className="nowrun__empty">— idle · nothing in production</div>
          )}
          {m.inProduction.map((r) => {
            const amber = r.kind === 'media';
            return (
              <div
                key={r.kind + r.ref_id}
                className={`nowrun__job${amber ? ' nowrun__job--media' : ''}`}
                onClick={() =>
                  onOpenTask &&
                  r.kind === 'content' &&
                  onOpenTask({ id: r.ref_id })
                }
              >
                <div className="nowrun__jobtop">
                  <span className="nowrun__jobtitle">{r.title}</span>
                  <span
                    className={`nowrun__age${amber ? ' nowrun__age--amber' : ''}`}
                  >
                    {fmtAge(r.elapsedS)}
                  </span>
                </div>
                <div className="nowrun__jobmeta">
                  <span
                    className={`nowrun__stage${amber ? ' nowrun__stage--amber' : ''}`}
                  >
                    {r.step || (amber ? 'rendering' : '')}
                  </span>
                  <span className="nowrun__dimmeta">
                    {r.progress_pct != null ? r.progress_pct + '%' : ''}
                  </span>
                </div>
                {r.progress_pct != null && (
                  <div className="nowrun__bar">
                    <div
                      className={`nowrun__fill${amber ? ' nowrun__fill--amber' : ''}`}
                      style={{ width: r.progress_pct + '%' }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="nowrun__col">
          <div className="nowrun__collabel">BACKGROUND</div>
          {m.background.length === 0 && (
            <div className="nowrun__empty">— idle</div>
          )}
          {m.background.map((r) => (
            <div key={r.kind + r.ref_id} className="nowrun__vrow">
              <span className="nowrun__dot" style={{ marginRight: 8 }} />
              <span
                className="grow"
                style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {r.title}
              </span>
              <span className="nowrun__age">{fmtAge(r.elapsedS)}</span>
            </div>
          ))}
        </div>
        <div className="nowrun__col">
          <div className="nowrun__collabel">JUST HAPPENED</div>
          {m.trail.length === 0 && <div className="nowrun__empty">—</div>}
          {m.trail.map((r, i) => (
            <div key={i} className="nowrun__trow">
              <span className={r.status === 'ok' ? 'ok' : 'fail'}>
                {glyph[r.status] || '·'}
              </span>
              <span>{r.title}</span>
              <span className="dur">
                {r.durationMs != null
                  ? Math.round(r.durationMs / 100) / 10 + 's'
                  : ''}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
window.NowRunningBand = NowRunningBand;
```

- [ ] **Step 3: Add trail-row CSS** to `console.css` if `.nowrun__trow` / `.ok` / `.fail` / `.dur` aren't defined (reuse the mockup styling):

```css
.nowrun__trow {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-family: var(--gl-font-mono);
  font-size: 10.5px;
  padding: 4px 0;
  color: var(--gl-text-muted);
}
.nowrun__trow .ok {
  color: var(--gl-mint);
}
.nowrun__trow .fail {
  color: var(--gl-red);
}
.nowrun__trow .dur {
  color: var(--gl-text-dim);
  margin-left: auto;
}
```

- [ ] **Step 4: Verify render (mock mode) + the taskStatusKind fix.** The band now sources from `activity` (ledger rows), NOT `pipeline.tasks` filtered by `taskStatusKind`, so terminal tasks can no longer appear as "running." Confirm by serving the console and inspecting:

Run: create `.claude/launch.json` (gitignored) with a `python -m http.server 8099 --directory src/cofounder_agent/console` config, `preview_start`, then `preview_eval`:

```javascript
(() => {
  const cols = document.querySelectorAll('.nowrun__col');
  return {
    cols: cols.length,
    inProd: document.querySelectorAll('.nowrun__col:nth-child(1) .nowrun__job')
      .length,
    label0: document.querySelector('.nowrun__collabel')?.textContent,
  };
})();
```

Expected: `cols: 3`, `label0: "IN PRODUCTION"`, `inProd: 2` (the 2 mock production rows).

- [ ] **Step 5: Run the full console suite**

Run: `npm run test:console 2>&1 | tail -5`
Expected: all pass (153+ existing + the Task 9 activity mapper cases).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/nowrunning.jsx src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/css/console.css
git commit -m "feat(activity): reframe NOW RUNNING band to the live pulse (In Production / Background / Just Happened)"
```

---

## Self-Review

_(completed after drafting — see the message accompanying this plan for the coverage/placeholder/type checks; fixes applied inline.)_

## Deferred to later phases (explicitly NOT in this plan)

- **Persistent cross-mode strip** (Phase 1.5) — a new consumer of `PX.api.activity()`.
- **WALL pulse** (`WallDisplay` enhancement, Phase 2).
- **Media render progress** instrumentation (Phase 2) — the `media_pipeline` render atoms emitting `live_activity.update(step, pct)`. Until then, media rendering surfaces as the `dispatch_media_pipeline` **job** row (Task 5) — job-level liveness.
- **C consolidation** (Phase 3) — migrate APScheduler jobs onto Prefect; Prefect writes the same `job` rows.
