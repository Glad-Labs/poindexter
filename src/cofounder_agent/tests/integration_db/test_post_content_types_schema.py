"""Schema verification for the post_content_types table (migration 20260713).

The classifier job + Dev.to gate depend on this table's shape: a multi-label
(post_id, content_type) axis with a UNIQUE key so a post can carry several
types but never the same one twice. Exercised against the migrated schema so a
future migration edit that drops a column or the unique key fails loudly here.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_post_content_types_table_shape(test_pool):
    async with test_pool.acquire() as conn:
        cols = {
            r["column_name"]: r["data_type"]
            for r in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'post_content_types'"
            )
        }
    assert {"id", "post_id", "content_type", "confidence", "source", "created_at"} <= set(cols)
    assert cols["post_id"] == "uuid"
    assert cols["content_type"] == "text"


async def test_post_content_types_has_multilabel_unique(test_pool):
    async with test_pool.acquire() as conn:
        uniques = await conn.fetch(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'post_content_types'::regclass AND contype = 'u'"
        )
    assert uniques, "expected a UNIQUE(post_id, content_type) constraint"


async def test_post_content_types_cascades_on_post_delete(test_pool):
    """Deleting a post drops its content-type rows (ON DELETE CASCADE)."""
    import uuid

    pid = uuid.uuid4()
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO posts (id, title, slug, status, content) "
                "VALUES ($1, 'cascade', 'pct-cascade', 'published', 'body')",
                pid,
            )
            await conn.execute(
                "INSERT INTO post_content_types (post_id, content_type) VALUES ($1, 'ai-ml')",
                pid,
            )
            await conn.execute("DELETE FROM posts WHERE id = $1", pid)
            remaining = await conn.fetchval(
                "SELECT count(*) FROM post_content_types WHERE post_id = $1", pid
            )
    assert remaining == 0
