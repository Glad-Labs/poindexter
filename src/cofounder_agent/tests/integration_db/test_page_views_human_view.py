"""page_views_human excludes is_bot rows; lab_outcomes_v1 reads the human view.

Runs against the disposable integration_db harness (tests/integration_db/
conftest.py applies the full services/migrations/ tree, including the
20260710 is_bot migration). Skips when no Postgres is reachable.
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


async def test_page_views_human_hides_is_bot_column(test_pool) -> None:
    """The human view hides the bot-flag machinery, nothing else.

    Asserted as "bot columns absent" + "data columns present" rather than as a
    frozen equality set: the view legitimately widens when page_views grows a
    reader-facing dimension (ref_source, 20260831), and an equality assertion
    turns every such widening into a false failure. What this test actually
    guards is that the flagging apparatus never leaks to reader surfaces.
    """
    async with test_pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'page_views_human'"
        )
    names = {r["column_name"] for r in cols}
    assert names.isdisjoint({"is_bot", "bot_reason", "flagged_at"})
    assert {
        "id", "path", "slug", "referrer", "user_agent", "created_at", "ref_source",
    } <= names


async def test_lab_outcomes_v1_still_queryable(test_pool) -> None:
    """The repointed lab_outcomes_v1 view still resolves (reads page_views_human)."""
    async with test_pool.acquire() as conn:
        # Empty result is fine; this asserts the view compiles + is selectable
        # after the CREATE OR REPLACE swap to page_views_human.
        await conn.fetch("SELECT task_id, views_24h_post_publish FROM lab_outcomes_v1 LIMIT 1")
