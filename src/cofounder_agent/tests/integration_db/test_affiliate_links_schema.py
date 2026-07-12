"""Schema guard for the affiliate-link tables.

Asserts the ``add_affiliate_links_tables`` migration created ``affiliate_links``
and ``affiliate_link_clicks`` with the columns the injection service, sync job,
and Grafana panels depend on. Runs against the session-scoped ``test_pool``
fixture (full migration tree applied to a disposable DB); skips cleanly when no
live Postgres is available.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _columns(pool, table: str) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1",
            table,
        )
    return {r["column_name"] for r in rows}


async def test_affiliate_links_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_links")
    assert {
        "id", "code", "keyword", "url", "display_text",
        "program", "is_active", "clicks", "created_at", "updated_at",
    } <= cols


async def test_affiliate_link_clicks_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_link_clicks")
    assert {
        "id", "code", "post_slug", "referrer",
        "country", "user_agent", "created_at",
    } <= cols


async def test_affiliate_links_ship_empty(test_pool):
    """Real referral rows are DB-only (CLI-added); the migration seeds none."""
    async with test_pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM affiliate_links")
    assert n == 0


async def test_affiliate_links_new_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_links")
    assert {"description", "category"} <= cols


async def test_affiliate_links_category_check_constraint(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO affiliate_links (code, keyword, url, category) "
                    "VALUES ('bad-cat-test', 'BadCat', 'https://x', 'bogus')"
                )
