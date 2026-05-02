"""Unit tests for ``services/jobs/backfill_post_performance_gsc.py``.

Coverage:

- Job metadata.
- Empty external_metrics → 0 rows changed.
- Pre-existing post_performance row with google_impressions=0 + GSC
  data in external_metrics → row patched in place.
- Pre-existing post_performance row with google_impressions>0 → NOT
  patched (we only fill in zeros).
- ``dry_run`` config flag → no UPDATE happens.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.jobs.backfill_post_performance_gsc import (
    BackfillPostPerformanceGscJob,
)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackfillPostPerformanceGscJobMetadata:
    def test_name_matches_registry_key(self):
        assert (
            BackfillPostPerformanceGscJob.name
            == "backfill_post_performance_gsc"
        )

    def test_idempotent(self):
        # Same SQL produces same result — safe to re-run.
        assert BackfillPostPerformanceGscJob.idempotent is True


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


async def _wipe(pool):
    """Reset the tables this job touches. Includes ``rollup-test-%``
    cleanup so cross-module test pollution can't leave stale rows that
    one of these test cases would mistake for fresh setup state.
    """
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM post_performance")
        await conn.execute("DELETE FROM external_metrics")
        await conn.execute(
            "DELETE FROM posts WHERE slug LIKE 'backfill-test-%' "
            "OR slug LIKE 'rollup-test-%'"
        )


async def _post(pool, *, slug: str) -> str:
    post_id = str(uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO posts (id, slug, title, content, status, published_at)
            VALUES ($1::uuid, $2, $3, $4, 'published', NOW() - INTERVAL '5 days')
            """,
            post_id, slug, f"Title {slug}", "Body for backfill test.",
        )
    return post_id


async def _existing_snapshot(
    pool,
    *,
    post_id: str,
    slug: str,
    google_impressions: int = 0,
) -> None:
    """Insert a pre-existing post_performance row with the given GSC count."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO post_performance
              (post_id, slug, views_1d, views_7d, views_30d, views_total,
               google_impressions, google_clicks, google_avg_position,
               measured_at, period)
            VALUES ($1::uuid, $2, 0, 0, 0, 0, $3, 0, NULL, NOW(), 'snapshot')
            """,
            post_id, slug, google_impressions,
        )


async def _gsc(
    pool,
    *,
    slug: str,
    metric_name: str,
    metric_value: float,
    days_ago: int = 1,
) -> None:
    date_value = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO external_metrics
              (source, metric_name, metric_value, dimensions,
               post_id, slug, date, fetched_at)
            VALUES ('google_search_console', $1, $2, '{}'::jsonb,
                    NULL, $3, $4, NOW())
            """,
            metric_name, metric_value, slug, date_value,
        )


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestBackfillPostPerformanceGscJobRun:
    async def test_empty_external_metrics_changes_nothing(self, db_pool):
        await _wipe(db_pool)
        post_id = await _post(db_pool, slug="backfill-test-empty")
        await _existing_snapshot(
            db_pool, post_id=post_id, slug="backfill-test-empty",
        )

        job = BackfillPostPerformanceGscJob()
        result = await job.run(db_pool, {})
        assert result.ok is True
        assert result.changes_made == 0

    async def test_patches_zero_impressions_row_from_gsc(self, db_pool):
        await _wipe(db_pool)
        post_id = await _post(db_pool, slug="backfill-test-patch")
        await _existing_snapshot(
            db_pool, post_id=post_id, slug="backfill-test-patch",
            google_impressions=0,
        )
        await _gsc(db_pool, slug="backfill-test-patch",
                   metric_name="impressions", metric_value=42, days_ago=2)
        await _gsc(db_pool, slug="backfill-test-patch",
                   metric_name="clicks", metric_value=7, days_ago=2)
        await _gsc(db_pool, slug="backfill-test-patch",
                   metric_name="position", metric_value=2.5, days_ago=2)

        job = BackfillPostPerformanceGscJob()
        result = await job.run(db_pool, {})
        assert result.ok is True
        assert result.changes_made == 1

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT google_impressions, google_clicks, google_avg_position "
                "FROM post_performance WHERE slug = $1",
                "backfill-test-patch",
            )
        assert row["google_impressions"] == 42
        assert row["google_clicks"] == 7
        assert row["google_avg_position"] == pytest.approx(2.5, abs=0.01)

    async def test_does_not_overwrite_nonzero_existing(self, db_pool):
        """Rows with google_impressions > 0 should be preserved — only
        zero rows are eligible for backfill, so we don't squash a fresh
        rollup result with stale GSC data.
        """
        await _wipe(db_pool)
        post_id = await _post(db_pool, slug="backfill-test-nonzero")
        await _existing_snapshot(
            db_pool, post_id=post_id, slug="backfill-test-nonzero",
            google_impressions=999,
        )
        await _gsc(db_pool, slug="backfill-test-nonzero",
                   metric_name="impressions", metric_value=10, days_ago=2)

        job = BackfillPostPerformanceGscJob()
        result = await job.run(db_pool, {})
        assert result.ok is True
        # No update because the existing row already has impressions=999.
        assert result.changes_made == 0

        async with db_pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT google_impressions FROM post_performance "
                "WHERE slug = $1",
                "backfill-test-nonzero",
            )
        assert value == 999

    async def test_dry_run_makes_no_changes(self, db_pool):
        await _wipe(db_pool)
        post_id = await _post(db_pool, slug="backfill-test-dryrun")
        await _existing_snapshot(
            db_pool, post_id=post_id, slug="backfill-test-dryrun",
            google_impressions=0,
        )
        await _gsc(db_pool, slug="backfill-test-dryrun",
                   metric_name="impressions", metric_value=50, days_ago=2)

        job = BackfillPostPerformanceGscJob()
        result = await job.run(db_pool, {"dry_run": True})
        assert result.ok is True
        assert result.changes_made == 0
        assert "DRY-RUN" in result.detail

        async with db_pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT google_impressions FROM post_performance "
                "WHERE slug = $1",
                "backfill-test-dryrun",
            )
        assert value == 0
