"""
Migration: Add estimated_cost column to content_tasks table

This migration adds cost tracking to content_tasks table so that:
1. Task creation endpoints can store estimated costs
2. Analytics can calculate cost metrics
3. Dashboard can display cost breakdown

Migration SQL:
- ALTER TABLE content_tasks ADD COLUMN estimated_cost DECIMAL(10,6) DEFAULT 0.0;
- ALTER TABLE content_tasks ADD COLUMN actual_cost DECIMAL(10,6) DEFAULT NULL;
- ALTER TABLE content_tasks ADD COLUMN cost_breakdown JSONB DEFAULT NULL;

These columns store:
- estimated_cost: Calculated at task creation time based on model selection
- actual_cost: Updated when task completes with real token usage
- cost_breakdown: JSON with per-phase costs {phase: cost, ...}
"""

import logging
import os
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to add cost columns to content_tasks"""
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    
    logger.info(f"Connecting to database: {database_url[:50]}...")
    
    try:
        # Create connection pool
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        
        async with pool.acquire() as conn:
            # Check if columns already exist
            result = await conn.fetch("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'content_tasks' 
                AND column_name IN ('estimated_cost', 'actual_cost', 'cost_breakdown')
            """)
            
            existing_columns = {row['column_name'] for row in result}
            logger.info(f"Existing cost columns: {existing_columns}")
            
            # Add estimated_cost if needed
            if 'estimated_cost' not in existing_columns:
                logger.info("Adding estimated_cost column...")
                await conn.execute("""
                    ALTER TABLE content_tasks 
                    ADD COLUMN estimated_cost DECIMAL(10,6) DEFAULT 0.0
                """)
                logger.info("✅ Added estimated_cost column")
            else:
                logger.info("✓ estimated_cost column already exists")
            
            # Add actual_cost if needed
            if 'actual_cost' not in existing_columns:
                logger.info("Adding actual_cost column...")
                await conn.execute("""
                    ALTER TABLE content_tasks 
                    ADD COLUMN actual_cost DECIMAL(10,6) DEFAULT NULL
                """)
                logger.info("✅ Added actual_cost column")
            else:
                logger.info("✓ actual_cost column already exists")
            
            # Add cost_breakdown if needed
            if 'cost_breakdown' not in existing_columns:
                logger.info("Adding cost_breakdown column...")
                await conn.execute("""
                    ALTER TABLE content_tasks 
                    ADD COLUMN cost_breakdown JSONB DEFAULT NULL
                """)
                logger.info("✅ Added cost_breakdown column")
            else:
                logger.info("✓ cost_breakdown column already exists")
            
            # Verify columns
            result = await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'content_tasks' 
                AND column_name IN ('estimated_cost', 'actual_cost', 'cost_breakdown')
                ORDER BY column_name
            """)
            
            logger.info("✅ Migration complete!")
            logger.info("Cost columns:")
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
