#!/usr/bin/env python3
"""
Migration script to add title column to content_tasks table
This script should be run against the local development database
"""

import asyncio
import logging
from pathlib import Path
import asyncpg
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to add title column"""
    
    # Get database URL
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/glad_labs_dev")
    
    logger.info(f"Connecting to database: {db_url}")
    
    try:
        # Connect to database
        conn = await asyncpg.connect(db_url)
        logger.info("✅ Connected to database")
        
        # Check if title column already exists
        result = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'content_tasks' AND column_name = 'title'
            )
        """)
        
        if result:
            logger.info("✅ Title column already exists, skipping migration")
            await conn.close()
            return
        
        logger.info("Adding title column to content_tasks...")
        
        # Add title column
        await conn.execute("ALTER TABLE content_tasks ADD COLUMN title VARCHAR(500)")
        logger.info("✅ Title column added")
        
        # Create index
        await conn.execute("CREATE INDEX idx_content_tasks_title ON content_tasks(title)")
        logger.info("✅ Index created")
        
        # Update existing tasks with NULL titles (set to topic as fallback)
        result = await conn.execute("UPDATE content_tasks SET title = topic WHERE title IS NULL")
        logger.info(f"✅ Updated existing tasks - {result}")
        
        # Get statistics
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_tasks,
                COUNT(CASE WHEN title IS NOT NULL THEN 1 END) as tasks_with_title,
                COUNT(CASE WHEN title IS NULL THEN 1 END) as tasks_with_null_title
            FROM content_tasks
        """)
        
        logger.info(f"✅ Migration complete!")
        logger.info(f"   Total tasks: {stats['total_tasks']}")
        logger.info(f"   Tasks with title: {stats['tasks_with_title']}")
        logger.info(f"   Tasks with NULL title: {stats['tasks_with_null_title']}")
        
        await conn.close()
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_migration())
