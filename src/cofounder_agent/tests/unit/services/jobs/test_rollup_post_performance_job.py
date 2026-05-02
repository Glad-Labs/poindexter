"""Unit tests for ``services/jobs/rollup_post_performance.py``.

Uses the ``db_pool`` fixture (real Postgres against a disposable test
database that the conftest creates per session and tears down at exit).
We avoid the row-faker pattern per Glad-Labs/poindexter#27's
"don't hand-roll asyncpg row mocks" guidance — a parallel agent is
migrating other suites away from that pattern.

Coverage:

- Job metadata (``name``, ``schedule``, idempotency contract).
- Empty DB → snapshots zero published posts → ``ok=True, changes_made=0``.
- Published post + page_views → snapshot row inserted with views_*.
- Published post + GSC external_metrics → snapshot row enriched with
  ``google_impressions`` / ``google_clicks`` / ``google_avg_position``.
- The ``min_published_days_ago`` config gates posts too new to roll up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.jobs.rollup_post_performance import RollupPostPerformanceJob


# ---------------------------------------------------------------------------
# Metadata (no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollupPostPerformanceJobMetadata:
    def test_name_matches_registry_key(self):
        assert RollupPostPerformanceJob.name == "rollup_post_performance"

    def test_idempotent_is_false(self):
        # Multiple runs intentionally create multiple snapshot rows — the
        # measured_at timestamp disambiguates. Documented behavior.
        assert RollupPostPerformanceJob.idempotent is False

    def test_schedule_is_human_readable(self):
        assert "every" in RollupPostPerformanceJob.schedule.lower()


# ---------------------------------------------------------------------------
# Behavior — uses the real DB via db_pool
# ---------------------------------------------------------------------------


async def _ensure_page_views_table(pool):
    """``page_views`` is created lazily by ``sync_page_views`` job — not
    by a migration. The rollup job reads it, so the test DB needs it
    explicitly. Mirror the schema from sync_page_views.py exactly.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_views (
                id SERIAL PRIMARY KEY,
                path TEXT,
                slug TEXT,
                referrer TEXT,
                user_agent TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )


async def _wipe_perf_tables(pool):
    """Reset the tables this job touches so cross-test rows don't leak.

    We delete every test-prefixed slug (``rollup-test-%`` and ``backfill-
    test-%``) because the backfill_post_performance_gsc_job test module
    can run before this one in the same process, and its rows would
    otherwise contaminate this test's "top 5 / snapshotted N posts"
    counts.
    """
    await _ensure_page_views_table(pool)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM post_performance")
        await conn.execute("DELETE FROM external_metrics")
        await conn.execute("DELETE FROM page_views")
        await conn.execute(
            "DELETE FROM posts WHERE slug LIKE 'rollup-test-%' "
            "OR slug LIKE 'backfill-test-%'"
        )


async def _insert_test_post(
    pool,
    *,
    slug: str,
    published_days_ago: int = 5,
    status: str = "published",
) -> str:
    """Insert a minimal posts row and return its id (str)."""
    published_at = datetime.now(timezone.utc) - timedelta(days=published_days_ago)
    post_id = str(uuid4())
    async with pool.acquire() as conn:
        # posts schema varies — set the known-required columns and let the
        # rest take their defaults. A NOT NULL column we don't provide
        # would surface as a clean asyncpg error, which is itself a
        # signal the test setup needs fixing.
        await conn.execute(
            """
            INSERT INTO posts (id, slug, title, content, status, published_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            """,
            post_id, slug,
            f"Test post {slug}",
            "Body for the rollup test.",
            status,
            published_at,
        )
    return post_id


async def _insert_page_views(pool, *, slug: str, count: int, age_hours: int = 1) -> None:
    """Insert ``count`` page_views rows targeting the given slug."""
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    async with pool.acquire() as conn:
        for i in range(count):
            await conn.execute(
                """
                INSERT INTO page_views (path, slug, created_at)
                VALUES ($1, $2, $3)
                """,
                f"/posts/{slug}", slug, ts + timedelta(seconds=i),
            )


async def _insert_gsc_metric(
    pool,
    *,
    slug: str,
    metric_name: str,
    metric_value: float,
    days_ago: int = 1,
) -> None:
    """Insert one external_metrics row mimicking the GSC tap output."""
    date_value = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO external_metrics
              (source, metric_name, metric_value, dimensions,
               post_id, slug, date, fetched_at)
            VALUES ($1, $2, $3, $4::jsonb, NULL, $5, $6, NOW())
            """,
            "google_search_console",
            metric_name,
            metric_value,
            "{}",
            slug,
            date_value,
        )


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestRollupPostPerformanceJobRun:
    async def test_empty_db_returns_zero_changes(self, db_pool):
        await _wipe_perf_tables(db_pool)
        job = RollupPostPerformanceJob()
        result = await job.run(db_pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "snapshotted 0 posts" in result.detail

    async def test_published_post_with_views_creates_snapshot(self, db_pool):
        await _wipe_perf_tables(db_pool)
        await _insert_test_post(db_pool, slug="rollup-test-views", published_days_ago=5)
        await _insert_page_views(db_pool, slug="rollup-test-views", count=4, age_hours=2)

        job = RollupPostPerformanceJob()
        result = await job.run(db_pool, {})

        assert result.ok is True
        assert result.changes_made == 1

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT views_1d, views_7d, views_30d, views_total, "
                "google_impressions, google_clicks, google_avg_position "
                "FROM post_performance WHERE slug = $1",
                "rollup-test-views",
            )
        assert row is not None
        assert row["views_1d"] == 4
        assert row["views_7d"] == 4
        assert row["views_30d"] == 4
        assert row["views_total"] == 4
        # No external_metrics → google columns default to 0 / NULL.
        assert row["google_impressions"] == 0
        assert row["google_clicks"] == 0
        assert row["google_avg_position"] is None

    async def test_gsc_external_metrics_enriches_snapshot(self, db_pool):
        """The Glad-Labs/poindexter#27 fix — GSC LEFT JOIN populates google_*."""
        await _wipe_perf_tables(db_pool)
        await _insert_test_post(db_pool, slug="rollup-test-gsc")
        # Two impressions rows + one clicks row + two position rows over
        # the past few days. SUM(impressions)=150, SUM(clicks)=12,
        # AVG(position)=3.5 (3.0 and 4.0 averaged).
        await _insert_gsc_metric(db_pool, slug="rollup-test-gsc",
                                 metric_name="impressions", metric_value=100, days_ago=2)
        await _insert_gsc_metric(db_pool, slug="rollup-test-gsc",
                                 metric_name="impressions", metric_value=50, days_ago=5)
        await _insert_gsc_metric(db_pool, slug="rollup-test-gsc",
                                 metric_name="clicks", metric_value=12, days_ago=2)
        await _insert_gsc_metric(db_pool, slug="rollup-test-gsc",
                                 metric_name="position", metric_value=3.0, days_ago=2)
        await _insert_gsc_metric(db_pool, slug="rollup-test-gsc",
                                 metric_name="position", metric_value=4.0, days_ago=5)

        job = RollupPostPerformanceJob()
        result = await job.run(db_pool, {})

        assert result.ok is True
        assert result.changes_made == 1

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT google_impressions, google_clicks, google_avg_position "
                "FROM post_performance WHERE slug = $1",
                "rollup-test-gsc",
            )
        assert row is not None
        assert row["google_impressions"] == 150
        assert row["google_clicks"] == 12
        assert row["google_avg_position"] == pytest.approx(3.5, abs=0.01)

    async def test_gsc_window_days_excludes_old_metrics(self, db_pool):
        """gsc_window_days config should clip out external_metrics outside the window."""
        await _wipe_perf_tables(db_pool)
        await _insert_test_post(db_pool, slug="rollup-test-window")
        # One inside window (5d ago), one outside (60d ago).
        await _insert_gsc_metric(db_pool, slug="rollup-test-window",
                                 metric_name="impressions", metric_value=10, days_ago=5)
        await _insert_gsc_metric(db_pool, slug="rollup-test-window",
                                 metric_name="impressions", metric_value=900, days_ago=60)

        job = RollupPostPerformanceJob()
        result = await job.run(db_pool, {"gsc_window_days": 30})
        assert result.ok is True

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT google_impressions FROM post_performance WHERE slug = $1",
                "rollup-test-window",
            )
        # Only the 5d-ago row counts; 60d-ago is outside the 30d window.
        assert row["google_impressions"] == 10

    async def test_min_published_days_ago_skips_too_new_posts(self, db_pool):
        await _wipe_perf_tables(db_pool)
        # Published 0 days ago — too new when min_published_days_ago=2.
        await _insert_test_post(db_pool, slug="rollup-test-toonew",
                                published_days_ago=0)

        job = RollupPostPerformanceJob()
        result = await job.run(db_pool, {"min_published_days_ago": 2})
        assert result.ok is True
        assert result.changes_made == 0

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM post_performance WHERE slug = $1",
                "rollup-test-toonew",
            )
        assert count == 0
