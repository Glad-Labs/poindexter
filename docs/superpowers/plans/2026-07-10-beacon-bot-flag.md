# Beacon Bot-Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De-bot the first-party `page_views` beacon so the console "views" KPI, `posts.view_count`, and `lab_outcomes_v1` reflect human readers, not the scraper that inflates the beacon ~10× (one Linux `Chrome/149` UA = 90% of a 28-day window).

**Architecture:** Materialize a `page_views.is_bot` flag populated by a windowed `(user_agent, path)` flood-cap sweep job; expose a `page_views_human` view; repoint the three reader-facing consumers to it while leaving liveness/anomaly/freshness signals on raw `page_views`. Bot classification (GROUP BY … HAVING) lives in SQL and is verified with `integration_db` tests — no parallel Python classifier (that would be testing-theater off the real path).

**Tech Stack:** Python 3.12, asyncpg, FastAPI, PostgreSQL, pytest (`unit` + `integration_db` markers), apscheduler-driven jobs registered in `plugins/registry.py`.

## Global Constraints

- **Config in DB, not code.** All tunables are `app_settings` keys seeded in `services/settings_defaults.py` (`DEFAULTS: dict[str, str]`), never hardcoded literals. `''` is the unset sentinel; values are always strings; never `NULL`.
- **New schema DDL goes in a fresh `YYYYMMDD_HHMMSS_<slug>.py` migration** with module-level `async def up(pool)` / `async def down(pool)`, stdlib-only (no app imports — migrations-smoke applies it without a full boot). Do **not** edit `0000_baseline.schema.sql` (it is a squash-time snapshot; the runner sorts it first and the new migration converges prod + fresh installs via `ADD COLUMN IF NOT EXISTS`).
- **Seed values go in `settings_defaults.py`, not migrations** (the seeder runs every boot; a migration runs once).
- **Fail loud, no silent defaults** (`feedback_no_silent_defaults`): a disabled feature returns an explicit skip `JobResult`, it does not silently no-op mid-logic.
- **Every change ships tests + docs** (`feedback_docs_and_tests_default`).
- **Linear history, conventional commits**, each ending with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Commit after each task.
- **Branch:** `claude/beacon-bot-flag` (already created; the design spec is committed there).
- **The raw-vs-human boundary is load-bearing:** raw `page_views` = "is the pipe flowing?" (liveness/anomaly/freshness — Grafana alerts, received-stat, funnel `is_viewed`, DB freshness/row-count). `page_views_human` = "how many readers?" (console `/api/analytics/views`, `posts.view_count`, `lab_outcomes_v1`). Do not move liveness signals onto the human view.

---

### Task 1: Seed the tunable settings

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (add keys near the existing "Cloudflare page-views beacon outage probe" block, ~line 1872, after `'cloudflare_beacon_url': ''`)
- Test: `src/cofounder_agent/tests/unit/services/test_beacon_bot_flag_defaults.py` (create)

**Interfaces:**

- Produces: `app_settings` keys `beacon_bot_flag_enabled`, `beacon_flood_window_hours`, `beacon_flood_cap_per_window`, `beacon_flood_backfill_cap` — read by Task 3's job via `site_config.get(key, default)`.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/tests/unit/services/test_beacon_bot_flag_defaults.py`:

```python
"""Guard: the beacon bot-flag tunables ship as app_settings defaults."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_beacon_bot_flag_defaults_present():
    assert DEFAULTS["beacon_bot_flag_enabled"] == "true"
    assert DEFAULTS["beacon_flood_window_hours"] == "24"
    assert DEFAULTS["beacon_flood_cap_per_window"] == "20"
    assert DEFAULTS["beacon_flood_backfill_cap"] == "30"


def test_beacon_bot_flag_defaults_are_strings():
    # app_settings values are always strings; '' is the unset sentinel, never NULL.
    for key in (
        "beacon_bot_flag_enabled",
        "beacon_flood_window_hours",
        "beacon_flood_cap_per_window",
        "beacon_flood_backfill_cap",
    ):
        assert isinstance(DEFAULTS[key], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_beacon_bot_flag_defaults.py -q`
Expected: FAIL with `KeyError: 'beacon_bot_flag_enabled'`

- [ ] **Step 3: Add the defaults**

In `services/settings_defaults.py`, immediately after the `'cloudflare_beacon_url': '',` line, insert:

```python
    # ----- Beacon bot-flag (de-bot the first-party page_views KPI) -----
    # Stealth scrapers present a browser User-Agent and slip the sync job's
    # narrow UA drop-filter, inflating page_views ~10x. FlagBotPageViewsJob
    # (services/jobs/flag_bot_page_views.py) sweeps accumulated rows and flags
    # any (user_agent, path) pair that floods past the cap. Reader-facing
    # surfaces read page_views_human (is_bot=false); liveness signals stay raw.
    # Master switch; 'false' makes the sweep job a no-op.
    'beacon_bot_flag_enabled': 'true',
    # Rolling window (hours) the flood pass groups over.
    'beacon_flood_window_hours': '24',
    # Max hits one (user_agent, path) pair may have in the window before the
    # WHOLE pair is flagged as bot. At current traffic no real pair reaches 20
    # same-UA hits/day; the bot pairs are in the hundreds. Tune up as traffic grows.
    'beacon_flood_cap_per_window': '20',
    # All-history cap for the one-time backfill pass (catches bots that flooded
    # historically but aren't active in the current window).
    'beacon_flood_backfill_cap': '30',
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_beacon_bot_flag_defaults.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_beacon_bot_flag_defaults.py
git commit -m "$(cat <<'EOF'
feat(analytics): seed beacon bot-flag tunables

is_bot sweep job config: enable switch, flood window, per-window cap,
backfill cap. All app_settings defaults, tunable per feedback_db_first_config.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Migration — is_bot column, indexes, page_views_human view, lab_outcomes_v1 repoint

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated>_page_views_is_bot_flag.py` (timestamp from `scripts/new-migration.py`)
- Modify: `src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py:184-192` (add columns to inline `CREATE TABLE IF NOT EXISTS`)
- Modify: `src/cofounder_agent/tests/integration_db/test_baseline_flatten_parity.py` (add parity guards)
- Test: `src/cofounder_agent/tests/integration_db/test_page_views_human_view.py` (create)

**Interfaces:**

- Produces: column `page_views.is_bot boolean NOT NULL DEFAULT false` (+ `bot_reason text`, `flagged_at timestamptz`); view `page_views_human(id, path, slug, referrer, user_agent, created_at)`; `lab_outcomes_v1` reading `page_views_human`. Consumed by Tasks 3, 5.

- [ ] **Step 1: Write the failing integration_db test**

Create `src/cofounder_agent/tests/integration_db/test_page_views_human_view.py`:

```python
"""page_views_human excludes is_bot rows; lab_outcomes_v1 reads the human view.

Runs against the disposable integration_db harness (tests/integration_db/
conftest.py applies the full services/migrations/ tree, including this
migration). Skips when no Postgres is reachable.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_page_views_human_excludes_bot_rows(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM page_views")
            await conn.execute(
                "INSERT INTO page_views (path, slug, referrer, user_agent, created_at, is_bot) "
                "VALUES ('/posts/a', 'a', '', 'human-ua', now(), false), "
                "       ('/posts/a', 'a', '', 'bot-ua',   now(), true)"
            )
            raw = await conn.fetchval("SELECT COUNT(*) FROM page_views")
            human = await conn.fetchval("SELECT COUNT(*) FROM page_views_human")
            raise_on_leak = await conn.fetchval(
                "SELECT COUNT(*) FROM page_views_human WHERE is_bot IS NOT NULL"
            )  # column absent from the human view → this errors if is_bot leaks; see note
    assert raw == 2
    assert human == 1


async def test_is_bot_defaults_false(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM page_views")
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "VALUES ('/posts/b', 'b', 'ua', now())"
            )
            is_bot = await conn.fetchval("SELECT is_bot FROM page_views LIMIT 1")
    assert is_bot is False
```

> Note: delete the `raise_on_leak` line before Step 3 if you keep `is_bot` out of `page_views_human` (it will error because the column is intentionally not selected). It is included here only to document that the human view must NOT expose `is_bot`; remove it and rely on `test_page_views_human_excludes_bot_rows`.

Simplify the first test to drop the `raise_on_leak` line:

```python
async def test_page_views_human_excludes_bot_rows(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM page_views")
            await conn.execute(
                "INSERT INTO page_views (path, slug, referrer, user_agent, created_at, is_bot) "
                "VALUES ('/posts/a', 'a', '', 'human-ua', now(), false), "
                "       ('/posts/a', 'a', '', 'bot-ua',   now(), true)"
            )
            raw = await conn.fetchval("SELECT COUNT(*) FROM page_views")
            human = await conn.fetchval("SELECT COUNT(*) FROM page_views_human")
    assert raw == 2
    assert human == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_page_views_human_view.py -q`
Expected: FAIL — `column "is_bot" of relation "page_views" does not exist` (and `relation "page_views_human" does not exist`).

- [ ] **Step 3: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "page views is bot flag and human view"`
This prints a path like `services/migrations/20260710_HHMMSS_page_views_is_bot_flag_and_human_view.py`. Replace its entire contents with:

```python
"""Migration: page_views.is_bot flag + page_views_human view + lab_outcomes_v1 repoint.

De-bots the first-party beacon KPI. Stealth scrapers present a browser UA and
slip the sync job's narrow UA drop-filter, inflating page_views ~10x (one Linux
Chrome/149 UA = 90% of a 28-day window). This migration adds the materialized
classification surface; FlagBotPageViewsJob populates it. The one-time backfill
of existing rows lives in that job (a migration runs once; the job's
sentinel-guarded backfill is re-evaluated every boot and is correct on fresh
installs).

Idempotent DDL — ADD COLUMN / CREATE INDEX IF NOT EXISTS no-op on fresh installs
(where nothing yet has the column) and do the real add on prod (baseline
predates is_bot). lab_outcomes_v1 is reproduced verbatim from the baseline with
the single change page_views -> page_views_human in its LATERAL subquery; the
column list is unchanged, so the dependent view experiment_variant_scorecard_v1
is unaffected.

stdlib-only so migrations-smoke applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LAB_OUTCOMES_BODY = """
CREATE OR REPLACE VIEW public.lab_outcomes_v1 AS
 SELECT co.task_id,
    co.niche_slug,
    co.template_slug,
    co.atom_name,
    co.model_used,
    co.prompt_template_key,
    co.prompt_template_version,
    co.ok AS atom_ok,
    co.halted AS atom_halted,
    co.quality_score AS atom_quality_score,
    co.elapsed_ms,
    co.created_at AS run_at,
    ro.actual_cost,
    ro.estimated_cost,
    ro.compute_tier,
    ro.success AS routing_success,
    pem.approver,
    pem.char_diff_count,
    pem.line_diff_count,
    pem.pre_approve_len,
    pem.post_approve_len,
    pem.approve_method,
    pem.approved_at,
    pv_count.views_24h AS views_24h_post_publish,
    pv_count.views_7d AS views_7d_post_publish,
    ev.label AS variant_label,
    ev.id AS variant_id,
    e.key AS experiment_key,
    e.status AS experiment_status,
    e.objective_function AS experiment_objective_function
   FROM (((((public.capability_outcomes co
     LEFT JOIN public.routing_outcomes ro ON (((ro.task_id)::text = co.task_id)))
     LEFT JOIN public.published_post_edit_metrics pem ON ((pem.task_id = co.task_id)))
     LEFT JOIN LATERAL ( SELECT count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '24:00:00'::interval)))) AS views_24h,
            count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '7 days'::interval)))) AS views_7d
           FROM ({page_views_ref} pv
             JOIN public.posts p ON (((p.slug)::text = (pv.slug)::text)))
          WHERE ((pem.approved_at IS NOT NULL) AND ((p.metadata ->> 'pipeline_task_id'::text) = co.task_id))) pv_count ON (true))
     LEFT JOIN public.experiment_variants ev ON ((ev.id = co.variant_id)))
     LEFT JOIN public.experiments e ON ((e.id = ev.experiment_id)))
  WHERE (co.created_at > (now() - '90 days'::interval));
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                ALTER TABLE page_views
                    ADD COLUMN IF NOT EXISTS is_bot boolean NOT NULL DEFAULT false,
                    ADD COLUMN IF NOT EXISTS bot_reason text,
                    ADD COLUMN IF NOT EXISTS flagged_at timestamp with time zone
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_page_views_human_created "
                "ON page_views (created_at) WHERE is_bot = false"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_page_views_human_slug "
                "ON page_views (slug) WHERE is_bot = false"
            )
            await conn.execute(
                "CREATE OR REPLACE VIEW public.page_views_human AS "
                "SELECT id, path, slug, referrer, user_agent, created_at "
                "FROM public.page_views WHERE is_bot = false"
            )
            await conn.execute(
                _LAB_OUTCOMES_BODY.format(page_views_ref="public.page_views_human")
            )
    logger.info(
        "page_views_is_bot_flag up: is_bot column + page_views_human view + "
        "lab_outcomes_v1 repointed to human view"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Repoint lab_outcomes_v1 back to raw page_views BEFORE dropping the
            # human view it now depends on.
            await conn.execute(
                _LAB_OUTCOMES_BODY.format(page_views_ref="public.page_views")
            )
            await conn.execute("DROP VIEW IF EXISTS public.page_views_human")
            await conn.execute("DROP INDEX IF EXISTS idx_page_views_human_created")
            await conn.execute("DROP INDEX IF EXISTS idx_page_views_human_slug")
            await conn.execute(
                "ALTER TABLE page_views "
                "DROP COLUMN IF EXISTS is_bot, "
                "DROP COLUMN IF EXISTS bot_reason, "
                "DROP COLUMN IF EXISTS flagged_at"
            )
    logger.info("page_views_is_bot_flag down: reverted to raw page_views")
```

- [ ] **Step 4: Add the columns to the sync job's inline CREATE TABLE (fresh-install parity)**

In `services/jobs/sync_cloudflare_analytics.py`, change the `CREATE TABLE IF NOT EXISTS page_views (...)` block (lines 184-192) to include the new columns:

```python
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS page_views (
                        id SERIAL PRIMARY KEY,
                        path TEXT,
                        slug TEXT,
                        referrer TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        is_bot BOOLEAN NOT NULL DEFAULT false,
                        bot_reason TEXT,
                        flagged_at TIMESTAMP WITH TIME ZONE
                    )
                    """
                )
```

- [ ] **Step 5: Run the new test + migration lint/smoke to verify they pass**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_page_views_human_view.py -q`
Expected: PASS (2 passed)

Run: `cd src/cofounder_agent && python ../../scripts/ci/migrations_lint.py` (adjust path to `scripts/ci/migrations_lint.py` from repo root)
Expected: exit 0 (no collisions / interface errors)

Run: `python scripts/ci/migrations_smoke.py`
Expected: exit 0 (fresh-DB apply succeeds; row-count parity holds)

- [ ] **Step 6: Add parity guards**

In `tests/integration_db/test_baseline_flatten_parity.py`:

- Add `"page_views_human"` to the `_REQUIRED_VIEWS` tuple (after `"lab_outcomes_v1"`).
- Add `("page_views", "is_bot")` to the `_REQUIRED_COLUMNS` tuple (with a comment `# 20260710 beacon bot-flag`).

- [ ] **Step 7: Run the parity test**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_baseline_flatten_parity.py -q`
Expected: PASS (existing tests still green; `page_views_human` view + `is_bot` column now asserted present).

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/services/migrations/ src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py src/cofounder_agent/tests/integration_db/test_page_views_human_view.py src/cofounder_agent/tests/integration_db/test_baseline_flatten_parity.py
git commit -m "$(cat <<'EOF'
feat(analytics): page_views.is_bot + page_views_human view + lab_outcomes repoint

Adds the materialized bot-classification surface. page_views_human filters
is_bot=false; lab_outcomes_v1 reproduced verbatim from baseline with the single
page_views -> page_views_human swap (column list unchanged, dependent scorecard
view unaffected). Backfill of existing rows lives in the job, not this migration.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: FlagBotPageViewsJob (sweep + backfill + view_count recompute)

**Files:**

- Create: `src/cofounder_agent/services/jobs/flag_bot_page_views.py`
- Modify: `src/cofounder_agent/plugins/registry.py:595-599` (register the job after the `SyncCloudflareAnalyticsJob` tuple)
- Test: `src/cofounder_agent/tests/integration_db/test_flag_bot_page_views_job.py` (create)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_flag_bot_page_views_gate.py` (create)

**Interfaces:**

- Consumes: `app_settings` keys from Task 1 (via `config["_site_config"].get`); `page_views.is_bot` + `page_views_human` from Task 2.
- Produces: class `FlagBotPageViewsJob` with `name = "flag_bot_page_views"`, `async def run(self, pool, config) -> JobResult`. Registered `("jobs", "services.jobs.flag_bot_page_views", "FlagBotPageViewsJob")`.

- [ ] **Step 1: Write the failing unit test (enable-gate no-op)**

Create `src/cofounder_agent/tests/unit/services/jobs/test_flag_bot_page_views_gate.py`:

```python
"""FlagBotPageViewsJob honors the master switch without touching the DB."""
from __future__ import annotations

import pytest

from services.jobs.flag_bot_page_views import FlagBotPageViewsJob


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


class _ExplodingPool:
    def acquire(self):  # pragma: no cover - must never be called
        raise AssertionError("pool.acquire() called while disabled")


@pytest.mark.asyncio
async def test_disabled_switch_skips_without_db():
    job = FlagBotPageViewsJob()
    config = {"_site_config": _FakeSiteConfig({"beacon_bot_flag_enabled": "false"})}
    result = await job.run(_ExplodingPool(), config)
    assert result.ok is True
    assert result.changes_made == 0
    assert "disabled" in result.detail.lower() or "false" in result.detail.lower()


@pytest.mark.asyncio
async def test_missing_site_config_skips():
    job = FlagBotPageViewsJob()
    result = await job.run(_ExplodingPool(), {})
    assert result.ok is True
    assert result.changes_made == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_flag_bot_page_views_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.jobs.flag_bot_page_views'`

- [ ] **Step 3: Write the job**

Create `src/cofounder_agent/services/jobs/flag_bot_page_views.py`:

```python
"""FlagBotPageViewsJob — flag stealth scraper hits in page_views.

The sync job (sync_cloudflare_analytics) drops declared-crawler UAs at ingest,
but stealth scrapers present a normal browser UA and slip through, inflating the
first-party beacon KPI ~10x. Declared bots can be caught per-row; a stealth
flood can only be caught over a WINDOW — a single 5-min sync batch can't tell one
(user_agent, path) has been hit hundreds of times over weeks. So this runs as a
periodic sweep over accumulated rows.

Per run (gated by app_settings.beacon_bot_flag_enabled):

1. Windowed flood pass — any (user_agent, path) pair with COUNT(*) over
   beacon_flood_cap_per_window inside beacon_flood_window_hours has its ENTIRE
   history flagged is_bot=true (whole-group semantics; biases to under-count over
   inflation). Flagging is monotonic: only ever sets is_bot true, never resets.
2. One-time backfill — sentinel-guarded (beacon_bot_flag_backfilled), same
   grouping over all history at beacon_flood_backfill_cap, to catch bots that
   flooded historically but aren't active in the current window.
3. Recompute posts.view_count authoritatively from page_views_human (this job is
   the single writer of view_count; the sync job's incremental bump is removed in
   the same PR). Bot-only posts reset to 0.

Reader surfaces (console /api/analytics/views, lab_outcomes_v1, posts.view_count)
read the human view; liveness/anomaly/freshness signals stay on raw page_views.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

_WINDOW_UPDATE = """
WITH flooded AS (
    SELECT user_agent, path
    FROM page_views
    WHERE created_at >= now() - ($1 || ' hours')::interval
      AND is_bot = false
    GROUP BY user_agent, path
    HAVING COUNT(*) > $2
)
UPDATE page_views pv
SET is_bot = true, bot_reason = 'flood:ua_path', flagged_at = now()
FROM flooded f
WHERE pv.user_agent IS NOT DISTINCT FROM f.user_agent
  AND pv.path IS NOT DISTINCT FROM f.path
  AND pv.is_bot = false
"""

_BACKFILL_UPDATE = """
WITH flooded AS (
    SELECT user_agent, path
    FROM page_views
    WHERE is_bot = false
    GROUP BY user_agent, path
    HAVING COUNT(*) > $1
)
UPDATE page_views pv
SET is_bot = true, bot_reason = 'flood:ua_path:backfill', flagged_at = now()
FROM flooded f
WHERE pv.user_agent IS NOT DISTINCT FROM f.user_agent
  AND pv.path IS NOT DISTINCT FROM f.path
  AND pv.is_bot = false
"""

_RECOMPUTE_VIEW_COUNT = """
UPDATE posts p
SET view_count = COALESCE(agg.c, 0)
FROM posts base
LEFT JOIN (
    SELECT slug, COUNT(*)::int AS c
    FROM page_views_human
    WHERE slug IS NOT NULL AND slug <> ''
    GROUP BY slug
) agg ON agg.slug = base.slug
WHERE p.id = base.id
  AND p.view_count IS DISTINCT FROM COALESCE(agg.c, 0)
"""

_SENTINEL_UPSERT = """
INSERT INTO app_settings (key, value, category, description, is_active, is_secret)
VALUES (
    'beacon_bot_flag_backfilled', 'true', 'cloudflare',
    'Set true after FlagBotPageViewsJob runs its one-time all-history bot backfill.',
    true, false
)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
"""


def _rowcount(status: Any) -> int:
    """Parse asyncpg's 'UPDATE N' command tag into an int."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


class FlagBotPageViewsJob:
    name = "flag_bot_page_views"
    description = (
        "Flag stealth-scraper page_views (windowed (user_agent, path) flood-cap) "
        "and recompute posts.view_count from the human view."
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no _site_config — skipping", changes_made=0)

        enabled = (sc.get("beacon_bot_flag_enabled", "true") or "true").strip().lower()
        if enabled != "true":
            return JobResult(
                ok=True,
                detail="beacon_bot_flag_enabled is disabled — skipping",
                changes_made=0,
            )

        window_hours = int((sc.get("beacon_flood_window_hours", "24") or "24").strip())
        window_cap = int((sc.get("beacon_flood_cap_per_window", "20") or "20").strip())
        backfill_cap = int((sc.get("beacon_flood_backfill_cap", "30") or "30").strip())

        flagged = 0
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    win = await conn.execute(_WINDOW_UPDATE, str(window_hours), window_cap)
                    flagged += _rowcount(win)

                    sentinel = await conn.fetchval(
                        "SELECT value FROM app_settings WHERE key = 'beacon_bot_flag_backfilled'"
                    )
                    did_backfill = False
                    if (sentinel or "").strip().lower() != "true":
                        back = await conn.execute(_BACKFILL_UPDATE, backfill_cap)
                        flagged += _rowcount(back)
                        await conn.execute(_SENTINEL_UPSERT)
                        did_backfill = True

                    await conn.execute(_RECOMPUTE_VIEW_COUNT)
        except Exception as e:  # noqa: BLE001 — surface as a failed JobResult, not a crash
            logger.exception("[FLAG_BOT_PV] sweep failed: %s", e)
            return JobResult(ok=False, detail=str(e), changes_made=0)

        return JobResult(
            ok=True,
            detail=f"flagged {flagged} bot page_views (backfill={did_backfill})",
            changes_made=flagged,
            metrics={"rows_flagged": flagged, "backfill_ran": int(did_backfill)},
        )
```

- [ ] **Step 4: Run the gate test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_flag_bot_page_views_gate.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Register the job**

In `plugins/registry.py`, immediately after the `SyncCloudflareAnalyticsJob` tuple (closing `),` at line 599), add:

```python
        # Stealth-bot sweep for the ingested page_views (the sync job above drops
        # only DECLARED crawler UAs; browser-UA scrapers slip through and inflate
        # the beacon KPI ~10x). Windowed (user_agent, path) flood-cap flags them
        # is_bot=true; reader surfaces read page_views_human. 15-min cadence.
        (
            "jobs",
            "services.jobs.flag_bot_page_views",
            "FlagBotPageViewsJob",
        ),
```

- [ ] **Step 6: Write the failing integration_db test (flagging + recompute + backfill sentinel)**

Create `src/cofounder_agent/tests/integration_db/test_flag_bot_page_views_job.py`:

```python
"""FlagBotPageViewsJob flags flood pairs, spares sparse traffic, recomputes view_count.

Runs against the integration_db harness (full migration tree applied). Skips
when no Postgres is reachable.
"""
from __future__ import annotations

import pytest

from services.jobs.flag_bot_page_views import FlagBotPageViewsJob

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _config():
    return {
        "_site_config": _FakeSiteConfig(
            {
                "beacon_bot_flag_enabled": "true",
                "beacon_flood_window_hours": "24",
                "beacon_flood_cap_per_window": "20",
                "beacon_flood_backfill_cap": "30",
            }
        )
    }


async def _reset(conn):
    await conn.execute("DELETE FROM page_views")
    await conn.execute("UPDATE app_settings SET value='' WHERE key='beacon_bot_flag_backfilled'")


async def test_flood_pair_flagged_sparse_spared(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            # 25 hits from one UA on one path within the window → over cap 20.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/hot', 'hot', 'flood-ua', now() FROM generate_series(1, 25)"
            )
            # 3 sparse human hits, distinct UAs, under cap.
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) VALUES "
                "('/posts/calm', 'calm', 'ua-1', now()), "
                "('/posts/calm', 'calm', 'ua-2', now()), "
                "('/posts/calm', 'calm', 'ua-3', now())"
            )

    result = await FlagBotPageViewsJob().run(test_pool, _config())
    assert result.ok is True

    async with test_pool.acquire() as conn:
        flood_flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='hot' AND is_bot=true"
        )
        calm_flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='calm' AND is_bot=true"
        )
        human = await conn.fetchval("SELECT COUNT(*) FROM page_views_human")
    assert flood_flagged == 25   # whole group flagged
    assert calm_flagged == 0     # sparse spared
    assert human == 3            # only the calm hits remain human


async def test_cap_boundary(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            # Exactly at cap (20) → NOT flagged (HAVING COUNT > cap).
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/edge', 'edge', 'edge-ua', now() FROM generate_series(1, 20)"
            )
    await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        flagged = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE slug='edge' AND is_bot=true"
        )
    assert flagged == 0


async def test_view_count_recomputed_from_human(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            await conn.execute("DELETE FROM posts WHERE slug IN ('hot', 'clean')")
            await conn.execute(
                "INSERT INTO posts (slug, title, status, view_count) VALUES "
                "('hot', 'Hot', 'published', 999), "
                "('clean', 'Clean', 'published', 0)"
            )
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) "
                "SELECT '/posts/hot', 'hot', 'flood-ua', now() FROM generate_series(1, 25)"
            )
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at) VALUES "
                "('/posts/clean', 'clean', 'ua-a', now()), "
                "('/posts/clean', 'clean', 'ua-b', now())"
            )
    await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        hot = await conn.fetchval("SELECT view_count FROM posts WHERE slug='hot'")
        clean = await conn.fetchval("SELECT view_count FROM posts WHERE slug='clean'")
    assert hot == 0    # bot-only post reset to 0
    assert clean == 2  # human views only


async def test_backfill_sentinel_runs_once(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
        result1 = None
    result1 = await FlagBotPageViewsJob().run(test_pool, _config())
    async with test_pool.acquire() as conn:
        sentinel = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key='beacon_bot_flag_backfilled'"
        )
    assert result1.metrics["backfill_ran"] == 1
    assert (sentinel or "").lower() == "true"
    # Second run: sentinel set → no backfill.
    result2 = await FlagBotPageViewsJob().run(test_pool, _config())
    assert result2.metrics["backfill_ran"] == 0
```

- [ ] **Step 7: Run the integration test to verify it passes**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_flag_bot_page_views_job.py -q`
Expected: PASS (4 passed)

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/services/jobs/flag_bot_page_views.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/integration_db/test_flag_bot_page_views_job.py src/cofounder_agent/tests/unit/services/jobs/test_flag_bot_page_views_gate.py
git commit -m "$(cat <<'EOF'
feat(analytics): FlagBotPageViewsJob — windowed (UA,path) flood-cap sweep

Flags stealth-scraper page_views the ingest UA-filter misses, backfills history
once (sentinel-guarded), and recomputes posts.view_count from page_views_human.
Whole-group flagging, monotonic. Registered on a 15-min cadence.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Make the flag job the single writer of posts.view_count

**Files:**

- Modify: `src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py:344-355` (remove the incremental `view_count` bump) and `276` (drop the now-unused `seen_slugs` accumulation if only used for the bump)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_sync_cloudflare_analytics_job.py` and/or `src/cofounder_agent/tests/integration/jobs/test_sync_cloudflare_analytics.py` (update any assertion that the sync job bumps `view_count`)

**Interfaces:**

- Consumes: `posts.view_count` is now owned by Task 3's recompute.
- Produces: sync job no longer writes `posts.view_count`.

- [ ] **Step 1: Find existing view_count assertions in the sync tests**

Run: `cd src/cofounder_agent && grep -rn "view_count" tests/unit/services/jobs/test_sync_cloudflare_analytics_job.py tests/integration/jobs/test_sync_cloudflare_analytics.py`
Note each assertion that expects the sync job to increment `posts.view_count`.

- [ ] **Step 2: Update the tests to assert the sync job does NOT touch view_count**

For each test found in Step 1 that asserts a `view_count` bump: replace the assertion so it verifies `posts.view_count` is unchanged by the sync job (the flag job owns it now). Example shape (adapt to the actual test's fixtures — it seeds a post, runs the job, then asserts):

```python
    # view_count is owned by FlagBotPageViewsJob (recompute from page_views_human),
    # NOT bumped by the sync ingest anymore.
    view_count_after = await conn.fetchval("SELECT view_count FROM posts WHERE slug = $1", slug)
    assert view_count_after == view_count_before
```

If a whole test exists solely to assert the bump, rename it to `test_sync_does_not_touch_view_count` and invert its assertion as above.

- [ ] **Step 3: Run the updated tests to verify they FAIL against current code**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration/jobs/test_sync_cloudflare_analytics.py tests/unit/services/jobs/test_sync_cloudflare_analytics_job.py -q`
Expected: FAIL — the sync job still bumps `view_count`, so the "unchanged" assertion fails.

- [ ] **Step 4: Remove the view_count bump from the sync job**

In `services/jobs/sync_cloudflare_analytics.py`, delete the bump block (lines 344-355):

```python
                    # Bump posts.view_count in lockstep — same behaviour the
                    # deleted /api/track/view handler had per-call. Done in
                    # one UPDATE per slug rather than per-row to keep the
                    # transaction tight.
                    for slug, delta in seen_slugs.items():
                        await conn.execute(
                            "UPDATE posts "
                            "SET view_count = COALESCE(view_count, 0) + $2 "
                            "WHERE slug = $1",
                            slug,
                            delta,
                        )
```

Replace it with a one-line comment:

```python
                    # posts.view_count is owned by FlagBotPageViewsJob (recompute
                    # from page_views_human) so bot inflation never reaches it.
```

Leave the `seen_slugs` dict in place — it still feeds the `distinct_slugs` metric and the `JobResult.detail`. (Verify: `seen_slugs` is referenced at lines ~369, ~374 for the detail/metrics — keep it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration/jobs/test_sync_cloudflare_analytics.py tests/unit/services/jobs/test_sync_cloudflare_analytics_job.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py src/cofounder_agent/tests/
git commit -m "$(cat <<'EOF'
refactor(analytics): sync job no longer writes posts.view_count

FlagBotPageViewsJob is the single writer of view_count (recompute from
page_views_human), so the sync ingest's incremental bump — which counted bots
before they could be flagged — is removed. No double-writer drift.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Repoint the console analytics endpoint to page_views_human

**Files:**

- Modify: `src/cofounder_agent/routes/cms_routes.py:698,705,713` (three `FROM page_views` → `FROM page_views_human`)
- Test: `src/cofounder_agent/tests/integration_db/test_analytics_views_human.py` (create)

**Interfaces:**

- Consumes: `page_views_human` view (Task 2).
- Produces: `/api/analytics/views` counts exclude `is_bot` rows.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/tests/integration_db/test_analytics_views_human.py`:

```python
"""The /api/analytics/views aggregations read page_views_human (bots excluded).

Verifies the endpoint's three queries behaviorally by running them against the
human view. Integration_db harness; skips without Postgres.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

# The three aggregations the endpoint runs (kept in sync with cms_routes.py).
_DAILY = (
    "SELECT date_trunc('day', created_at)::date as day, COUNT(*) as views "
    "FROM page_views_human WHERE created_at > NOW() - ($1 || ' days')::interval "
    "GROUP BY 1 ORDER BY 1"
)
_TOP = (
    "SELECT slug, COUNT(*) as views FROM page_views_human "
    "WHERE slug IS NOT NULL AND slug != '' "
    "AND created_at > NOW() - ($1 || ' days')::interval "
    "GROUP BY slug ORDER BY views DESC LIMIT 20"
)


async def test_analytics_views_exclude_bots(test_pool) -> None:
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM page_views")
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at, is_bot) "
                "SELECT '/posts/x', 'x', 'bot', now(), true FROM generate_series(1, 50)"
            )
            await conn.execute(
                "INSERT INTO page_views (path, slug, user_agent, created_at, is_bot) VALUES "
                "('/posts/x', 'x', 'human', now(), false), "
                "('/posts/x', 'x', 'human', now(), false)"
            )
            daily = await conn.fetch(_DAILY, "7")
            top = await conn.fetch(_TOP, "7")
    daily_total = sum(r["views"] for r in daily)
    assert daily_total == 2  # 50 bot hits excluded
    assert top[0]["slug"] == "x"
    assert top[0]["views"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_analytics_views_human.py -q`
Expected: FAIL — at this point the test file references `page_views_human` which exists (Task 2), so this test will actually PASS immediately since it embeds the human queries directly. To make it a true red-first for the ENDPOINT, first confirm the endpoint still reads raw by grepping:

Run: `cd src/cofounder_agent && grep -n "FROM page_views " routes/cms_routes.py`
Expected: three lines still reading `FROM page_views` (raw) — these are what Step 3 fixes. (The test guards the intended query shape; Step 3 makes the endpoint match it.)

- [ ] **Step 3: Repoint the endpoint**

In `routes/cms_routes.py`, in `get_view_stats`, change all three `FROM page_views` to `FROM page_views_human`:

- Line ~699: `"FROM page_views_human WHERE created_at > NOW() - ($1 || ' days')::interval "`
- Line ~705: `"SELECT slug, COUNT(*) as views FROM page_views_human "`
- Line ~713: `"SELECT referrer, COUNT(*) as views FROM page_views_human "`

- [ ] **Step 4: Verify the endpoint now matches the human queries**

Run: `cd src/cofounder_agent && grep -n "FROM page_views" routes/cms_routes.py`
Expected: three lines now read `FROM page_views_human`; zero `FROM page_views ` (raw) remain in `get_view_stats`.

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/integration_db/test_analytics_views_human.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/routes/cms_routes.py src/cofounder_agent/tests/integration_db/test_analytics_views_human.py
git commit -m "$(cat <<'EOF'
fix(console): /api/analytics/views reads page_views_human (bots excluded)

The console views KPI's three aggregations now exclude is_bot rows. Liveness /
anomaly / freshness signals stay on raw page_views by design.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Docs + memory

**Files:**

- Modify: `infrastructure/cloudflare/page-views-beacon/README.md` (note the is_bot flag + human view)
- Modify: `CLAUDE.md` (the `page_views` table bullet — add the is_bot/human-view note; this is not an auto-derived stat so it must be hand-edited)
- Create: `docs/architecture/page-views-bot-flag.md` (the raw-vs-human boundary reference)
- Modify (outside repo): `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/project_analytics_integrity_2026_07.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Write the architecture note**

Create `docs/architecture/page-views-bot-flag.md`:

```markdown
# page_views bot-flag & the raw-vs-human boundary

The first-party beacon (`CF Analytics Engine → sync_cloudflare_analytics →
page_views`) is inflated by stealth scrapers that present a browser
`User-Agent` and slip the ingest UA drop-filter. `FlagBotPageViewsJob`
(`services/jobs/flag_bot_page_views.py`, 15-min) flags them via a windowed
`(user_agent, path)` flood-cap, materialized as `page_views.is_bot`.

## Two surfaces

| Question             | Reads                               | Consumers                                                                                                                                   |
| -------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Is the pipe flowing? | raw `page_views`                    | Grafana traffic-anomaly + capture-dead alerts, Mission-Control "Page-views received", pipeline funnel `is_viewed`, DB freshness + row-count |
| How many readers?    | `page_views_human` (`is_bot=false`) | console `/api/analytics/views`, `posts.view_count`, `lab_outcomes_v1.views_*_post_publish`                                                  |

A bot hit still proves the URL is live and ingest is fresh, so liveness stays on
raw. Only reader counts move to the human view.

## Tunables (`app_settings`)

- `beacon_bot_flag_enabled` (default `true`) — master switch.
- `beacon_flood_window_hours` (`24`) — flood window.
- `beacon_flood_cap_per_window` (`20`) — per-`(UA,path)` cap; over → whole pair flagged.
- `beacon_flood_backfill_cap` (`30`) — one-time all-history backfill cap.

## Notes

- `posts.view_count` is recomputed from `page_views_human` by the flag job — it
  is the single writer. The sync ingest no longer bumps it.
- Flagging is monotonic (only sets `is_bot` true). To re-open after raising a
  cap: `UPDATE page_views SET is_bot=false WHERE bot_reason LIKE 'flood:%'` then
  let the next sweep re-evaluate.
- No IP signal exists on the beacon (CF bot-score is plan-gated); the flood-cap
  is the near-term heuristic. Bot rows are retained (audit), not dropped.
```

- [ ] **Step 2: Update the beacon README**

In `infrastructure/cloudflare/page-views-beacon/README.md`, add a short paragraph after the intro (near line 8) noting that ingested rows are swept by `FlagBotPageViewsJob` and that reader-facing surfaces read `page_views_human`, while the README's own liveness `COUNT(*)` examples intentionally stay on raw `page_views`.

- [ ] **Step 3: Update the CLAUDE.md page_views bullet**

In `CLAUDE.md`, find the database-tables bullet `- `page_views` — own analytics tracking` and replace with:

```markdown
- `page_views` — own analytics tracking. Ingested rows carry an `is_bot` flag set by `FlagBotPageViewsJob` (windowed `(user_agent, path)` flood-cap); reader surfaces (console `/api/analytics/views`, `posts.view_count`, `lab_outcomes_v1`) read the `page_views_human` view, while liveness/anomaly/freshness signals stay on raw `page_views`. See [`docs/architecture/page-views-bot-flag.md`](docs/architecture/page-views-bot-flag.md).
```

- [ ] **Step 4: Update the memory file**

Append to `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/project_analytics_integrity_2026_07.md` a dated line recording that PR 2 of 2 (beacon `is_bot` flag + `page_views_human` + flood-cap sweep job) shipped, with the tunable keys and the raw-vs-human boundary, and that `beacon=SoT` is now bot-filtered at the KPI layer.

- [ ] **Step 5: Verify doc links resolve**

Run: `cd src/cofounder_agent && python -c "import pathlib; assert pathlib.Path('../../docs/architecture/page-views-bot-flag.md').exists()"`
Expected: no error.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/architecture/page-views-bot-flag.md infrastructure/cloudflare/page-views-beacon/README.md
git commit -m "$(cat <<'EOF'
docs(analytics): document page_views bot-flag + raw-vs-human boundary

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Full verification + open PR

**Files:** none (verification + PR).

- [ ] **Step 1: Run the full affected test surface**

Run: `cd src/cofounder_agent && INTEGRATION_DB=1 poetry run pytest tests/unit/services/test_beacon_bot_flag_defaults.py tests/unit/services/jobs/test_flag_bot_page_views_gate.py tests/integration_db/test_page_views_human_view.py tests/integration_db/test_flag_bot_page_views_job.py tests/integration_db/test_analytics_views_human.py tests/integration_db/test_baseline_flatten_parity.py tests/integration_db/test_lab_outcomes_v1_view.py tests/integration/jobs/test_sync_cloudflare_analytics.py tests/unit/services/jobs/test_sync_cloudflare_analytics_job.py -q`
Expected: all PASS (existing `test_lab_outcomes_v1_view.py` still green — the repoint kept its column list).

- [ ] **Step 2: Run migration lint + smoke once more**

Run: `python scripts/ci/migrations_lint.py && python scripts/ci/migrations_smoke.py`
Expected: exit 0 for both.

- [ ] **Step 3: Lint/type-check touched Python**

Run: `cd src/cofounder_agent && poetry run ruff check services/jobs/flag_bot_page_views.py services/jobs/sync_cloudflare_analytics.py routes/cms_routes.py services/settings_defaults.py`
Expected: no errors.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin claude/beacon-bot-flag
```

- [ ] **Step 5: Open the PR against Glad-Labs/glad-labs-stack**

```bash
gh pr create --repo Glad-Labs/glad-labs-stack --base main --head claude/beacon-bot-flag \
  --title "fix(analytics): beacon bot-flag — de-bot the page_views KPI (PR 2 of 2)" \
  --body "$(cat <<'EOF'
## What

PR 2 of 2 of the 2026-07-03 analytics-integrity investigation (PR 1 = #2110 tap dedup). Materializes a `page_views.is_bot` flag populated by a windowed `(user_agent, path)` flood-cap sweep job, adds a `page_views_human` view, and repoints the reader-facing consumers to it.

Confirmed against prod (2026-06-11 → 07-08): beacon raw = 1,708 vs GA 178, where one Linux `Chrome/149` UA = 1,532 (90%). This is the actual 604-vs-86 console fix.

## Changes

- `page_views.is_bot` / `bot_reason` / `flagged_at` + `page_views_human` view + `lab_outcomes_v1` repoint (migration; baseline untouched per convention).
- `FlagBotPageViewsJob` (15-min): windowed flood-cap (whole-group, monotonic), one-time sentinel-guarded backfill, recompute `posts.view_count` from human.
- Sync job no longer writes `view_count` (single-writer).
- Console `/api/analytics/views` → `page_views_human`.
- Liveness/anomaly/freshness signals stay on raw `page_views` by design.

## Tunables (app_settings)

`beacon_bot_flag_enabled` (true), `beacon_flood_window_hours` (24), `beacon_flood_cap_per_window` (20), `beacon_flood_backfill_cap` (30).

## Testing

Unit (gate + defaults) + integration_db (human view, flood-flagging, cap boundary, view_count recompute, backfill sentinel, console exclusion) + baseline parity + migrations lint/smoke.

Design: `docs/superpowers/specs/2026-07-09-beacon-bot-flag-design.md`. Plan: `docs/superpowers/plans/2026-07-10-beacon-bot-flag.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Verify CI is green, then report the PR URL.** Per `feedback_ci_is_the_review_gate`, CI passing is the merge gate. Report the PR link and CI status back to Matt.

---

## Self-Review

**Spec coverage:**

- is_bot column + page_views_human + lab_outcomes repoint → Task 2 ✓
- FlagBotPageViewsJob (windowed flood, backfill, recompute) → Task 3 ✓
- Sync single-writer view_count → Task 4 ✓
- Console repoint → Task 5 ✓
- Tunables in settings_defaults → Task 1 ✓
- Raw-vs-human boundary (Grafana untouched) → documented Task 6 + Global Constraints ✓
- Tests (unit + integration_db + parity) → Tasks 1,2,3,4,5 ✓
- Docs → Task 6 ✓
- Deviations from spec, deliberate: (a) baseline schema.sql NOT edited (repo convention — new DDL lives in the migration; parity test is presence-based so it doesn't require it); (b) no pure `classify_flood_groups` helper (the aggregation is SQL `GROUP BY … HAVING`, tested via integration_db — a parallel Python classifier would be off the real path). Both noted in Architecture + Task 3.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. Migration filename is `<generated>` because `scripts/new-migration.py` mints the timestamp — the exact content to paste is given. The one embedded `raise_on_leak` teaching line in Task 2 Step 1 is explicitly flagged for deletion with a corrected test block immediately following.

**Type consistency:** `FlagBotPageViewsJob.run(self, pool, config) -> JobResult` consistent across Tasks 3 & 7; `_rowcount` used only within the job; `metrics["backfill_ran"]` (int 0/1) asserted in Task 3 Step 6 matches the job's `metrics={"backfill_ran": int(did_backfill)}`; `page_views_human` column list `(id, path, slug, referrer, user_agent, created_at)` consistent in Task 2 migration and consumers. Settings keys identical across Tasks 1 and 3.
