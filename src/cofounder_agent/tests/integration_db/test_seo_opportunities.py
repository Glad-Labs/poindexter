"""Integration tests for the seo_opportunities schema + analyzer roundtrip.

Requires a live Postgres (the integration_db tier). SKIPs otherwise.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_seo_opportunities_table_exists_with_expected_columns(test_pool):
    async with test_pool.acquire() as conn:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'seo_opportunities'"
            )
        }
    for expected in (
        "post_id",
        "slug",
        "target_query",
        "tier",
        "current_position",
        "impressions",
        "ctr",
        "gap_score",
        "status",
        "detected_at",
        "baseline_position",
        "baseline_ctr",
        "outcome_position",
        "outcome_ctr",
        "outcome_measured_at",
        "refreshed_at",
    ):
        assert expected in cols, f"missing column {expected}"


async def test_analyze_classifies_and_upserts(test_pool):
    from services.seo.striking_distance import DEFAULT_THRESHOLDS, analyze_and_upsert

    post_id = uuid4()
    slug = f"seo-test-{post_id.hex[:8]}"
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (id, title, slug, status, published_at) "
                "VALUES ($1, 'SEO Test', $2, 'published', NOW())",
                post_id,
                slug,
            )
            # pos 6, 500 impressions, 4 clicks → page1_push
            await conn.execute(
                "INSERT INTO post_performance "
                "(post_id, slug, google_impressions, google_clicks, "
                " google_avg_position, measured_at) "
                "VALUES ($1, $2, 500, 4, 6.0, NOW())",
                post_id,
                slug,
            )

        written = await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        assert written >= 1

        async with test_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tier, impressions, status, gap_score "
                "FROM seo_opportunities WHERE post_id = $1",
                post_id,
            )
        assert row is not None
        assert row["tier"] == "page1_push"
        assert row["impressions"] == 500
        assert row["status"] == "open"
        assert float(row["gap_score"]) > 0

        # Idempotent: a second run updates in place, no duplicate row.
        await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        async with test_pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM seo_opportunities WHERE post_id = $1",
                post_id,
            )
        assert n == 1
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM seo_opportunities WHERE post_id = $1", post_id
            )
            await conn.execute(
                "DELETE FROM post_performance WHERE post_id = $1", post_id
            )
            await conn.execute("DELETE FROM posts WHERE id = $1", post_id)


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
                post_id,
                slug,
            )
            await conn.execute(
                "INSERT INTO post_performance "
                "(post_id, slug, google_impressions, google_clicks, "
                " google_avg_position, measured_at) "
                "VALUES ($1, $2, 500, 4, 6.0, NOW())",
                post_id,
                slug,
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
                "WHERE post_id=$1",
                post_id,
            )
        assert row["status"] == "refreshed", "status latch failed — reverted to open"
        assert row["baseline_position"] is not None, "baseline lost on re-upsert"
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM seo_opportunities WHERE post_id=$1", post_id
            )
            await conn.execute(
                "DELETE FROM post_performance WHERE post_id=$1", post_id
            )
            await conn.execute("DELETE FROM posts WHERE id=$1", post_id)
