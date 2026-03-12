"""
Migration 0035: Drop posts.tag_ids denormalised array column.

Addresses issue #343: migration 008 added posts.tag_ids (UUID[]) as a denormalised store
for post-tag relationships. Migration 014 added the authoritative post_tags junction table
and backfilled it from tag_ids. Both representations now co-exist, and any write that
updates one without the other causes divergence.

content_db.py.create_post() was updated (in the same PR as this migration) to write to
both tag_ids and post_tags. Once this migration drops tag_ids, create_post() falls back to
writing only post_tags, which is the canonical source.

Changes:
1. Backfill post_tags from tag_ids for any posts not yet in the junction table.
2. Remove tag_ids from the INSERT + RETURNING queries in content_db.py (done in code).
3. Drop posts.tag_ids column.

Rollback: re-add tag_ids column and backfill from post_tags.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Backfill post_tags from tag_ids then drop posts.tag_ids column."""
    async with pool.acquire() as conn:
        posts_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'posts')"
        )
        post_tags_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'post_tags')"
        )

        if not posts_exists:
            logger.warning("Table 'posts' does not exist — skipping")
            return

        # Check if tag_ids column still exists
        col_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'posts' AND column_name = 'tag_ids'
            )
            """
        )
        if not col_exists:
            logger.info("posts.tag_ids column already dropped — no action needed")
            return

        if post_tags_exists:
            # Backfill post_tags for any posts with tag_ids not yet represented in junction table
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
            logger.info(f"Backfilled {backfilled or 0} post_tags rows from tag_ids before drop")

        # Drop the tag_ids column
        await conn.execute("ALTER TABLE posts DROP COLUMN IF EXISTS tag_ids")
        logger.info("Dropped posts.tag_ids column — post_tags is now the sole authoritative source")


async def down(pool) -> None:
    """Re-add posts.tag_ids and backfill from post_tags."""
    async with pool.acquire() as conn:
        posts_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'posts')"
        )
        if not posts_exists:
            return

        col_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'posts' AND column_name = 'tag_ids'
            )
            """
        )
        if not col_exists:
            await conn.execute(
                "ALTER TABLE posts ADD COLUMN tag_ids UUID[] DEFAULT '{}'"
            )
            logger.info("Re-added posts.tag_ids column")

        post_tags_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'post_tags')"
        )
        if post_tags_exists:
            await conn.execute(
                """
                UPDATE posts p
                SET tag_ids = (
                    SELECT array_agg(pt.tag_id)
                    FROM post_tags pt
                    WHERE pt.post_id = p.id
                )
                """
            )
            logger.info("Backfilled posts.tag_ids from post_tags (rollback of 0035)")
