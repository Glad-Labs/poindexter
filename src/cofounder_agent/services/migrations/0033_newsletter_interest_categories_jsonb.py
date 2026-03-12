"""
Migration 0033: Convert newsletter_subscribers.interest_categories from VARCHAR(500) to JSONB.

Addresses issue #334: interest_categories stores a JSON array as VARCHAR(500), causing:
- Silent truncation at 500 characters for subscribers with many interests.
- LIKE-based filtering that cannot use indexes.
- No JSON validity enforcement.

Changes:
1. Null out any non-JSON values before the type cast.
2. Cast the column to JSONB using CASE WHEN logic (empty strings become NULL).
3. Add a GIN index for fast containment queries (e.g. WHERE interest_categories @> '["AI"]').

Rollback: cast back to VARCHAR(500), drop GIN index.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Convert newsletter_subscribers.interest_categories to JSONB."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'newsletter_subscribers')"
        )
        if not table_exists:
            logger.warning("Table 'newsletter_subscribers' does not exist — skipping")
            return

        col_type = await conn.fetchval(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'newsletter_subscribers'
              AND column_name = 'interest_categories'
            """
        )
        if col_type == "jsonb":
            logger.info("interest_categories already JSONB — skipping type conversion")
        else:
            # Null out any values that are not valid JSON before casting
            bad_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM newsletter_subscribers
                WHERE interest_categories IS NOT NULL
                  AND interest_categories != ''
                  AND interest_categories::text NOT LIKE '[%'
                  AND interest_categories::text NOT LIKE '{%'
                """
            )
            if bad_count:
                logger.warning(
                    f"Nulling {bad_count} non-JSON interest_categories values before JSONB cast"
                )
                await conn.execute(
                    """
                    UPDATE newsletter_subscribers
                    SET interest_categories = NULL
                    WHERE interest_categories IS NOT NULL
                      AND interest_categories != ''
                      AND interest_categories::text NOT LIKE '[%'
                      AND interest_categories::text NOT LIKE '{%'
                    """
                )

            await conn.execute(
                """
                ALTER TABLE newsletter_subscribers
                    ALTER COLUMN interest_categories TYPE JSONB
                    USING CASE
                        WHEN interest_categories IS NULL OR interest_categories = '' THEN NULL
                        ELSE interest_categories::jsonb
                    END
                """
            )
            logger.info("Converted newsletter_subscribers.interest_categories to JSONB")

        # Add GIN index for containment queries if not present
        idx_exists = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_newsletter_interests_gin'"
        )
        if not idx_exists:
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_newsletter_interests_gin
                    ON newsletter_subscribers USING GIN(interest_categories)
                """
            )
            logger.info("Created GIN index idx_newsletter_interests_gin")
        else:
            logger.info("GIN index idx_newsletter_interests_gin already exists — skipping")


async def down(pool) -> None:
    """Revert interest_categories from JSONB to VARCHAR(500)."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'newsletter_subscribers')"
        )
        if not table_exists:
            return

        await conn.execute("DROP INDEX IF EXISTS idx_newsletter_interests_gin")

        await conn.execute(
            """
            ALTER TABLE newsletter_subscribers
                ALTER COLUMN interest_categories TYPE VARCHAR(500)
                USING interest_categories::text
            """
        )
        logger.info("Reverted newsletter_subscribers.interest_categories to VARCHAR(500)")
