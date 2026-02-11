import asyncio
import logging
import os
from services.database_service import DatabaseService
from services.migrations import MigrationService

logger = logging.getLogger(__name__)

async def run_migrations():
    # Initialize database service
    db_service = DatabaseService()
    await db_service.initialize()
    
    # Run migrations
    migration_service = MigrationService(db_service.pool)
    success = await migration_service.run_migrations()
    
    if success:
        logger.info('✅ Migrations completed successfully')
    else:
        logger.error('❌ Migrations failed')

if __name__ == "__main__":
    asyncio.run(run_migrations())