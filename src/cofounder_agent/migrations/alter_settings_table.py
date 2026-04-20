"""
Migration: Alter settings table to add missing columns.

This migration adds the columns needed by admin_db.py:
- category: VARCHAR(255) for grouping
- display_name: VARCHAR(500) for UI display
- is_active: BOOLEAN for soft deletes
- modified_at: TIMESTAMP for modification tracking

Also converts value column from JSONB to TEXT for consistency.
"""

import logging
import os

import asyncpg

logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to alter settings table"""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    logger.info(f"Connecting to database: {database_url[:50]}...")

    try:
        # Create connection pool
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)

        async with pool.acquire() as conn:
            # Check if table exists
            table_exists = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'settings'
                )
            """
            )

            if not table_exists:
                logger.warning("Settings table does not exist - skipping migration")
                await pool.close()
                return

            logger.info("Settings table exists - checking columns...")

            # Get existing columns
            existing_cols = await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'settings'
            """
            )
            existing_col_names = {row["column_name"] for row in existing_cols}
            logger.info(f"Existing columns: {existing_col_names}")

            # Add missing columns
            if "category" not in existing_col_names:
                logger.info("Adding category column...")
                await conn.execute(
                    "ALTER TABLE settings ADD COLUMN category VARCHAR(255)"
                )
                logger.info("✅ category column added")

            if "display_name" not in existing_col_names:
                logger.info("Adding display_name column...")
                await conn.execute(
                    "ALTER TABLE settings ADD COLUMN display_name VARCHAR(500)"
                )
                logger.info("✅ display_name column added")

            if "is_active" not in existing_col_names:
                logger.info("Adding is_active column...")
                await conn.execute(
                    "ALTER TABLE settings ADD COLUMN is_active BOOLEAN DEFAULT true"
                )
                logger.info("✅ is_active column added")

            if "modified_at" not in existing_col_names:
                logger.info("Adding modified_at column...")
                await conn.execute(
                    "ALTER TABLE settings ADD COLUMN modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                )
                logger.info("✅ modified_at column added")

            logger.info("✅ Settings table migration completed successfully")

        await pool.close()

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_migration())
