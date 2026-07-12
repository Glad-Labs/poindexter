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
        "id", "code", "url", "display_text",
        "program", "is_active", "clicks", "created_at", "updated_at",
        "description", "category", "platform",
    } <= cols
    assert "keyword" not in cols


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


async def test_affiliate_links_category_check_constraint(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO affiliate_links (code, url, category) "
                    "VALUES ('bad-cat-test', 'https://x', 'bogus')"
                )


async def test_affiliate_link_keywords_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_link_keywords")
    assert {"id", "link_id", "keyword", "created_at"} <= cols


async def test_affiliate_link_keywords_allows_shared_keyword_across_links(test_pool):
    """Different links CAN share a keyword — uniqueness is scoped
    UNIQUE(link_id, keyword), not global. See the design spec's "Shared
    keywords" section."""
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                link_a = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "shared-kw-test-a", "https://x",
                )
                link_b = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "shared-kw-test-b", "https://y",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_a, "SharedKeyword",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_b, "SharedKeyword",
                )
                # Force rollback so this test data never persists in the shared pool.
                raise RuntimeError("rollback-for-test-isolation")


async def test_affiliate_link_keywords_rejects_duplicate_within_same_link(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                link_id = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "dup-kw-test", "https://x",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_id, "DupKeyword",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_id, "DupKeyword",
                )
