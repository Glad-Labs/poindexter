"""
Migration 0026: Add missing database indexes.

Adds indexes identified in issues #265 and #317:
- posts.author_id — foreign key with no index causes sequential scan on every
  post-by-author lookup (e.g. author archive pages, permission checks).

Note: writing_samples.user_id and task_status_history.task_id already have
indexes defined in the SQL-based migrations (004 and 001 respectively) and do
not need to be added here.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Add missing indexes."""
    async with pool.acquire() as conn:
        # posts.author_id — FK with no index
        posts_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'posts')"
        )
        if posts_exists:
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_posts_author_id
                    ON posts(author_id)
                """
            )
            logger.info("Index idx_posts_author_id ensured")
        else:
            logger.warning("Table 'posts' does not exist — skipping idx_posts_author_id")


async def down(pool) -> None:
    """Remove indexes added by this migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_posts_author_id")
    logger.info("Removed idx_posts_author_id")
