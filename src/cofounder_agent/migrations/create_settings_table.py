"""
Migration: Create settings table for persistent configuration.

This migration creates a settings table to store:
- application settings (model preferences, feature flags, etc.)
- user preferences (UI settings, defaults, etc.)
- system configuration (timeouts, limits, etc.)

Schema:
- settings_id: UUID primary key
- key: VARCHAR unique identifier for setting
- value: JSONB for flexible value storage
- created_at: TIMESTAMP
- updated_at: TIMESTAMP
- description: VARCHAR optional description

Migration SQL:
CREATE TABLE IF NOT EXISTS settings (
    settings_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(255) UNIQUE NOT NULL,
    value JSONB DEFAULT NULL,
    description VARCHAR(1000),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_settings_key ON settings(key);
"""

import logging
import os
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to create settings table"""
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    
    logger.info(f"Connecting to database: {database_url[:50]}...")
    
    try:
        # Create connection pool
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        
        async with pool.acquire() as conn:
            # Check if table already exists
            result = await conn.fetch("""
                SELECT EXISTS(
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'settings'
                )
            """)
            
            table_exists = result[0]['exists'] if result else False
            
            if not table_exists:
                logger.info("Creating settings table...")
                await conn.execute("""
                    CREATE TABLE settings (
                        settings_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        key VARCHAR(255) UNIQUE NOT NULL,
                        value JSONB DEFAULT NULL,
                        description VARCHAR(1000),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("✅ Settings table created")
                
                # Create index on key for fast lookups
                logger.info("Creating index on settings(key)...")
                await conn.execute("""
                    CREATE INDEX idx_settings_key ON settings(key)
                """)
                logger.info("✅ Index created")
                
            else:
                logger.info("✓ Settings table already exists")
            
            # Verify table
            result = await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'settings'
                ORDER BY ordinal_position
            """)
            
            logger.info("✅ Migration complete!")
            logger.info("Settings table schema:")
            for row in result:
                logger.info(f"  - {row['column_name']}: {row['data_type']}")
        
        await pool.close()
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    import asyncio
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    asyncio.run(run_migration())
