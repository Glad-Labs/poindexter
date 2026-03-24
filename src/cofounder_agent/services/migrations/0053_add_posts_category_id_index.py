"""
Migration 0053: Add index on posts.category_id

Addresses issue #999: posts.category_id has no index, causing
sequential scans on JOIN and filtered queries.
"""

UP = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_category_id
ON posts (category_id);
"""

DOWN = """
DROP INDEX IF EXISTS idx_posts_category_id;
"""


async def up(pool):
    """Apply migration 0053: create index on posts.category_id."""
    async with pool.acquire() as conn:
        await conn.execute(UP)


async def down(pool):
    """Revert migration 0053: drop index on posts.category_id."""
    async with pool.acquire() as conn:
        await conn.execute(DOWN)
