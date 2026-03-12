"""
Migration 0034: Add FK constraints to social_post_analytics and web_analytics.

Addresses issue #353: both analytics tables reference posts/pages by VARCHAR(255) string
IDs with no FK constraints. posts.id is UUID. The type mismatch prevents index use on joins
and allows orphaned analytics rows to accumulate after post deletion.

Changes:
1. Verify/clean non-UUID post_id values in social_post_analytics.
2. Cast social_post_analytics.post_id to UUID.
3. Add FK from social_post_analytics.post_id -> posts(id) ON DELETE CASCADE.
4. web_analytics.page_id is nullable and references pages by string ID.
   The application has no 'pages' table — page_id is an arbitrary string identifier
   (URL path or page slug). A FK is not possible without a pages table.
   This migration documents that gap and adds an index on page_id for query performance.

Rollback: drop FK, cast social_post_analytics.post_id back to VARCHAR(255).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Add FK on social_post_analytics.post_id and document web_analytics limitation."""
    async with pool.acquire() as conn:
        # --- social_post_analytics ---
        spa_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'social_post_analytics')"
        )
        posts_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'posts')"
        )

        if not spa_exists:
            logger.warning("Table 'social_post_analytics' does not exist — skipping")
        elif not posts_exists:
            logger.warning("Table 'posts' does not exist — skipping FK creation")
        else:
            col_type = await conn.fetchval(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'social_post_analytics' AND column_name = 'post_id'
                """
            )
            fk_exists = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'social_post_analytics'
                  AND constraint_name = 'fk_social_analytics_post_id'
                  AND constraint_type = 'FOREIGN KEY'
                """
            )

            if col_type != "uuid":
                # Delete orphaned rows (post_id not in posts) before FK + type cast
                deleted = await conn.fetchval(
                    """
                    DELETE FROM social_post_analytics
                    WHERE post_id IS NOT NULL
                      AND post_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                      AND post_id::uuid NOT IN (SELECT id FROM posts)
                    """
                )
                if deleted:
                    logger.warning(f"Deleted {deleted} orphaned social_post_analytics rows")

                # Null out non-UUID post_id values
                await conn.execute(
                    """
                    UPDATE social_post_analytics
                    SET post_id = NULL
                    WHERE post_id IS NOT NULL
                      AND post_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    """
                )

                await conn.execute(
                    """
                    ALTER TABLE social_post_analytics
                        ALTER COLUMN post_id TYPE UUID USING post_id::uuid
                    """
                )
                logger.info("Converted social_post_analytics.post_id to UUID")

            if not fk_exists:
                await conn.execute(
                    """
                    ALTER TABLE social_post_analytics
                        ADD CONSTRAINT fk_social_analytics_post_id
                            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
                    """
                )
                logger.info("Added FK fk_social_analytics_post_id (ON DELETE CASCADE)")
            else:
                logger.info("FK fk_social_analytics_post_id already exists — skipping")

        # --- web_analytics ---
        wa_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'web_analytics')"
        )
        if wa_exists:
            # page_id is a string identifier (URL path / slug), not a UUID FK to a pages table.
            # No pages table exists in this schema. Add an index for query performance instead.
            idx_exists = await conn.fetchval(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_web_analytics_page_id'"
            )
            if not idx_exists:
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_web_analytics_page_id
                        ON web_analytics(page_id)
                    """
                )
                logger.info("Created idx_web_analytics_page_id")
            logger.info(
                "NOTE (issue #353): web_analytics.page_id is a URL-path string identifier — "
                "no FK added (no pages table exists). Add a pages table to enable FK enforcement."
            )
        else:
            logger.warning("Table 'web_analytics' does not exist — skipping")


async def down(pool) -> None:
    """Revert social_post_analytics.post_id type change and drop FK."""
    async with pool.acquire() as conn:
        spa_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'social_post_analytics')"
        )
        if spa_exists:
            await conn.execute(
                "ALTER TABLE social_post_analytics DROP CONSTRAINT IF EXISTS fk_social_analytics_post_id"
            )
            col_type = await conn.fetchval(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'social_post_analytics' AND column_name = 'post_id'
                """
            )
            if col_type == "uuid":
                await conn.execute(
                    """
                    ALTER TABLE social_post_analytics
                        ALTER COLUMN post_id TYPE VARCHAR(255) USING post_id::text
                    """
                )
                logger.info("Reverted social_post_analytics.post_id to VARCHAR(255)")

        wa_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'web_analytics')"
        )
        if wa_exists:
            await conn.execute("DROP INDEX IF EXISTS idx_web_analytics_page_id")
    logger.info("Reverted migration 0034 analytics FK changes")
