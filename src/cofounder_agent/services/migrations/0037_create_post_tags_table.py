"""
Migration 0037: Create post_tags junction table.

cms_routes.py JOINs on post_tags but the table was never created in the
Python migration sequence (raw SQL migration 014 exists but was never
applied — tracked in issue #469).

Changes:
1. Create post_tags(post_id, tag_id) junction table with FK constraints.
2. Add indexes on both FK columns.
3. Backfill from posts.tag_ids UUID[] column if it still exists.

Rollback: drop post_tags table (data loss: tag associations).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Create post_tags junction table and backfill from tag_ids if present."""
    async with pool.acquire() as conn:
        posts_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'posts')"
        )
        if not posts_exists:
            logger.warning("Table 'posts' does not exist — skipping post_tags creation")
            return

        tags_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'tags')"
        )

        already_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'post_tags')"
        )
        if already_exists:
            logger.info("post_tags table already exists — skipping creation")
            return

        # Create junction table — FK to tags only when tags table exists
        if tags_exists:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS post_tags (
                    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    tag_id  UUID NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
                    PRIMARY KEY (post_id, tag_id)
                )
                """
            )
        else:
            # No tags table yet — create without FK, add constraint later
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS post_tags (
                    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    tag_id  UUID NOT NULL,
                    PRIMARY KEY (post_id, tag_id)
                )
                """
            )
            logger.warning("tags table not found — post_tags.tag_id created without FK constraint")

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_post_tags_post_id ON post_tags(post_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_post_tags_tag_id ON post_tags(tag_id)"
        )
        logger.info("Created post_tags junction table with indexes")

        # Backfill from posts.tag_ids if the column still exists
        tag_ids_col_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'posts' AND column_name = 'tag_ids'
            )
            """
        )
        if tag_ids_col_exists:
            backfilled = await conn.fetchval(
                """
                WITH inserted AS (
                    INSERT INTO post_tags (post_id, tag_id)
                    SELECT p.id, unnest(p.tag_ids)
                    FROM posts p
                    WHERE p.tag_ids IS NOT NULL AND array_length(p.tag_ids, 1) > 0
                    ON CONFLICT (post_id, tag_id) DO NOTHING
                    RETURNING 1
                )
                SELECT COUNT(*) FROM inserted
                """
            )
            logger.info(f"Backfilled {backfilled or 0} post_tags rows from posts.tag_ids")
        else:
            logger.info("posts.tag_ids column not present — no backfill needed")


async def down(pool) -> None:
    """Drop the post_tags junction table."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS post_tags CASCADE")
        logger.info("Dropped post_tags junction table (rollback of 0037)")
