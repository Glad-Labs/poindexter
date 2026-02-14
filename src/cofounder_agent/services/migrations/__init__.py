"""
Database Migrations Runner

Dynamically discovers and runs all async migrations in the migrations/ directory.
Migration files should have two functions:
- async def up(pool): Apply the migration
- async def down(pool): Revert the migration

Migration files are run in alphabetical order by default.
"""

import importlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


async def run_migrations(database_service) -> bool:
    """
    Run all pending migrations in the migrations/ directory.
    
    Args:
        database_service: The DatabaseService instance with pool access
        
    Returns:
        bool: True if all migrations completed successfully, False otherwise
    """
    try:
        if not database_service or not database_service.pool:
            logger.warning("Database service or pool not available, skipping migrations")
            return False

        migrations_dir = Path(__file__).parent
        migration_files = sorted([f for f in migrations_dir.glob("*.py") if f.name != "__init__.py"])

        if not migration_files:
            logger.info("No migrations found")
            return True

        logger.info(f"Found {len(migration_files)} migration(s)")

        for migration_file in migration_files:
            migration_name = migration_file.name
            try:
                # Dynamically import the migration module
                spec = importlib.util.spec_from_file_location(migration_name[:-3], migration_file)
                migration_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration_module)

                # Check if the migration has an up() function
                if not hasattr(migration_module, "up"):
                    logger.warning(f"⚠️  Migration {migration_name} missing up() function, skipping")
                    continue

                logger.info(f"Applying migration: {migration_name}")
                await migration_module.up(database_service.pool)
                logger.info(f"✅ Migration completed: {migration_name}")

            except Exception as e:
                logger.error(f"❌ Migration failed: {migration_name}")
                logger.error(f"   Error: {str(e)}")
                return False

        logger.info("✅ All migrations completed successfully")
        return True

    except Exception as e:
        logger.error(f"❌ Migration runner error: {str(e)}")
        return False
