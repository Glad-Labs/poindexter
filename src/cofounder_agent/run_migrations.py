import asyncio
import os
from services.database_service import DatabaseService
from services.migrations import MigrationService

async def run_migrations():
    # Initialize database service
    db_service = DatabaseService()
    await db_service.initialize()
    
    # Run migrations
    migration_service = MigrationService(db_service.pool)
    success = await migration_service.run_migrations()
    
    if success:
        print('✅ Migrations completed successfully')
    else:
        print('❌ Migrations failed')

if __name__ == "__main__":
    asyncio.run(run_migrations())