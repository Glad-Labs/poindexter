"""
Migration: Alter settings table to add missing columns.

This migration adds the columns needed by admin_db.py:
- category: VARCHAR(255) for grouping
- display_name: VARCHAR(500) for UI display
- is_active: BOOLEAN for soft deletes
- modified_at: TIMESTAMP for modification tracking
"""

from services.logger_config import get_logger
import os

import asyncpg

logger = get_logger(__name__)
async def up(pool) -> None:
    """
    Apply migration: add missing columns to settings table.

    Called by the migrations runner (services/migrations/__init__.py).
    Uses the shared pool from DatabaseService — no separate connection needed.
    """
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
            logger.warning("Settings table does not exist — skipping migration")
            return

        logger.info("Settings table exists — checking for missing columns...")

        existing_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'settings'"
        )
        existing_col_names = {row["column_name"] for row in existing_cols}

        additions = [
            ("category", "ALTER TABLE settings ADD COLUMN category VARCHAR(255)"),
            ("display_name", "ALTER TABLE settings ADD COLUMN display_name VARCHAR(500)"),
            ("is_active", "ALTER TABLE settings ADD COLUMN is_active BOOLEAN DEFAULT true"),
            ("modified_at", "ALTER TABLE settings ADD COLUMN modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]

        for col_name, sql in additions:
            if col_name not in existing_col_names:
                logger.info(f"Adding column: {col_name}")
                await conn.execute(sql)
                logger.info(f"Column added: {col_name}")

        logger.info("Settings table migration completed")


async def run_migration():
    """Standalone entry point for running this migration directly (legacy)."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    logger.info(f"Connecting to database: {database_url[:50]}...")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    try:
        await up(pool)
    finally:
        await pool.close()
