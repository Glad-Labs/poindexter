"""
Standalone migration runner.

Usage (from src/cofounder_agent/):
    poetry run python run_migrations.py

This is a convenience wrapper for running migrations outside the normal
FastAPI startup sequence (e.g. in CI, pre-deploy scripts, or manual recovery).
At runtime, migrations are run automatically by startup_manager via
services/migrations/__init__.py.
"""

import asyncio

from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.migrations import run_migrations

logger = get_logger(__name__)


async def main():
    db_service = DatabaseService()
    await db_service.initialize()

    success = await run_migrations(db_service)

    if success:
        logger.info("Migrations completed successfully")
    else:
        logger.error("Migrations failed")


if __name__ == "__main__":
    asyncio.run(main())
