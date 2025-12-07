"""
Database Migration Service

Handles database schema migrations for PostgreSQL.
Runs migration files in order to ensure schema is up-to-date.

Usage:
    from services.migrations import run_migrations
    await run_migrations(database_service)
"""

import logging
import os
import asyncpg
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MigrationService:
    """Handles database schema migrations"""

    def __init__(self, pool: asyncpg.Pool):
        """Initialize migration service with database pool"""
        self.pool = pool
        self.migrations_dir = Path(__file__).parent.parent / "migrations"

    async def run_migrations(self) -> bool:
        """
        Run all pending migrations in order.
        
        Returns:
            True if migrations completed successfully, False if failed
        """
        try:
            # Create migrations_applied table if it doesn't exist
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS migrations_applied (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) UNIQUE NOT NULL,
                        applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
            # Get list of migration files
            migration_files = sorted(self.migrations_dir.glob("*.sql"))
            
            if not migration_files:
                logger.warning(f"No migration files found in {self.migrations_dir}")
                return True
            
            # Run each migration that hasn't been applied
            for migration_file in migration_files:
                await self._run_migration(migration_file)
            
            logger.info(f"✅ Migrations completed successfully ({len(migration_files)} files)")
            return True

        except Exception as e:
            logger.error(f"❌ Migration failed: {str(e)}", exc_info=True)
            return False

    async def _run_migration(self, migration_file: Path) -> bool:
        """
        Run a single migration file.
        
        Args:
            migration_file: Path to SQL migration file
            
        Returns:
            True if migration ran successfully, False if already applied or failed
        """
        try:
            migration_name = migration_file.name
            
            # Check if migration has already been applied
            async with self.pool.acquire() as conn:
                already_applied = await conn.fetchval(
                    "SELECT id FROM migrations_applied WHERE name = $1",
                    migration_name
                )
            
            if already_applied:
                logger.debug(f"⏭️  Skipping migration (already applied): {migration_name}")
                return True
            
            # Read migration SQL
            sql_content = migration_file.read_text()
            
            # Run migration
            logger.info(f"🔄 Running migration: {migration_name}")
            async with self.pool.acquire() as conn:
                # Execute migration
                await conn.execute(sql_content)
                
                # Record that migration was applied
                await conn.execute(
                    "INSERT INTO migrations_applied (name) VALUES ($1)",
                    migration_name
                )
            
            logger.info(f"✅ Migration completed: {migration_name}")
            return True

        except Exception as e:
            logger.error(f"❌ Migration failed ({migration_file.name}): {str(e)}", exc_info=True)
            raise

    async def rollback_last_migration(self) -> bool:
        """Rollback the last applied migration (for testing)"""
        try:
            async with self.pool.acquire() as conn:
                last_migration = await conn.fetchval(
                    "SELECT name FROM migrations_applied ORDER BY applied_at DESC LIMIT 1"
                )
            
            if not last_migration:
                logger.warning("No migrations to rollback")
                return False
            
            logger.info(f"Rolling back migration: {last_migration}")
            
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM migrations_applied WHERE name = $1",
                    last_migration
                )
            
            logger.info(f"✅ Rollback completed: {last_migration}")
            return True

        except Exception as e:
            logger.error(f"❌ Rollback failed: {str(e)}", exc_info=True)
            return False


async def run_migrations(database_service) -> bool:
    """
    Convenience function to run migrations.
    
    Args:
        database_service: DatabaseService instance with pool
        
    Returns:
        True if migrations completed successfully
    """
    if not database_service or not database_service.pool:
        logger.error("Database service not initialized")
        return False
    
    migration_service = MigrationService(database_service.pool)
    return await migration_service.run_migrations()
