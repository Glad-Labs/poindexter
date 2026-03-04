#!/usr/bin/env python3
"""Check workflow_executions table structure and recent entries"""

import asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev"

async def main():
    try:
        # Create async engine
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            # Get table structure
            result = await conn.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'workflow_executions'
                ORDER BY ordinal_position
            """))
            
            print("=" * 80)
            print("WORKFLOW_EXECUTIONS TABLE STRUCTURE")
            print("=" * 80)
            for row in result:
                print(f"{row[0]:30} {row[1]}")
            
            # Get recent executions
            result = await conn.execute(text("""
                SELECT id, execution_status, selected_model, created_at
                FROM workflow_executions
                ORDER BY created_at DESC
                LIMIT 5
            """))
            
            print("\n" + "=" * 80)
            print("RECENT EXECUTIONS")
            print("=" * 80)
            for row in result:
                print(f"ID: {str(row[0])[:36]}")
                print(f"  Status: {row[1]}")
                print(f"  Selected Model: {row[2]}")
                print(f"  Created: {row[3]}")
                print()
        
        await engine.dispose()
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
