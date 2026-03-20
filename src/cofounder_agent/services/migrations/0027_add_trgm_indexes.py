"""
Migration 0027: Add pg_trgm GIN indexes for ILIKE keyword search.

Addresses issue #307: ILIKE '%term%' keyword search on content_tasks
currently forces a full sequential scan because leading-wildcard patterns
cannot use standard B-tree indexes.

This migration:
1. Enables the pg_trgm extension (idempotent — no-op if already enabled).
2. Creates GIN trigram indexes on the columns used by the keyword-search
   filter in get_tasks_paginated():
     - content_tasks.title  (task display name / task_name)
     - content_tasks.topic
     - content_tasks.category

After this migration, PostgreSQL will use the GIN indexes for
  "title ILIKE '%term%' OR topic ILIKE '%term%' OR category ILIKE '%term%'"
predicates, eliminating the full sequential scan.

Rollback: DROP the three GIN indexes (the extension is left enabled as it
is safe to have and other queries may benefit from it).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Enable pg_trgm and create GIN trigram indexes on content_tasks."""
    async with pool.acquire() as conn:
        # Enable the trigram extension (idempotent)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        logger.info("pg_trgm extension ensured")

        # Verify content_tasks table exists before creating indexes
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            logger.warning("Table 'content_tasks' does not exist — skipping trgm indexes")
            return

        # GIN trigram index on title (task display name)
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_tasks_title_trgm
                ON content_tasks USING gin (title gin_trgm_ops)
            """
        )
        logger.info("Index idx_content_tasks_title_trgm ensured")

        # GIN trigram index on topic
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_tasks_topic_trgm
                ON content_tasks USING gin (topic gin_trgm_ops)
            """
        )
        logger.info("Index idx_content_tasks_topic_trgm ensured")

        # GIN trigram index on category
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_tasks_category_trgm
                ON content_tasks USING gin (category gin_trgm_ops)
            """
        )
        logger.info("Index idx_content_tasks_category_trgm ensured")


async def down(pool) -> None:
    """Remove the trigram indexes added by this migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_content_tasks_title_trgm")
        await conn.execute("DROP INDEX IF EXISTS idx_content_tasks_topic_trgm")
        await conn.execute("DROP INDEX IF EXISTS idx_content_tasks_category_trgm")
    logger.info("Removed trgm indexes from content_tasks")
    # Note: pg_trgm extension is intentionally left enabled
