import asyncio
import logging

from services.database_service import DatabaseService
from services.migrations import MigrationService
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


async def run_migrations():
    # Initialize database service. #272 Phase-2g: DatabaseService takes a
    # REQUIRED site_config; this standalone migration entrypoint has no
    # lifespan-bound instance in scope, so it passes a fresh env-fallback
    # SiteConfig() — identical to the empty module global this path used to
    # resolve at pool-creation time (pool sizes fall back to defaults).
    db_service = DatabaseService(site_config=SiteConfig())
    await db_service.initialize()

    # Run migrations
    migration_service = MigrationService(db_service.pool)
    success = await migration_service.run_migrations()

    if success:
        logger.info("✅ Migrations completed successfully")
    else:
        logger.error("❌ Migrations failed")


if __name__ == "__main__":
    asyncio.run(run_migrations())
