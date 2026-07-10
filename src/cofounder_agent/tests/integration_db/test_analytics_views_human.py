"""The /api/analytics/views aggregations read page_views_human (bots excluded).

Verifies the endpoint's queries behaviorally by running them against the human
view. Integration_db harness; skips without Postgres.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

# The two count aggregations the endpoint runs (kept in sync with cms_routes.py).
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
