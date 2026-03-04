#!/usr/bin/env python3
"""Quick test of the database persistence"""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev"

test_exec_id = "f8c87ca8-853b-4b46-b439-574b33883dd7"

async def main():
    try:
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            result = await conn.execute(text(f"""
                SELECT id, execution_status, selected_model, created_at
                FROM workflow_executions  
                WHERE id = '{test_exec_id}'
                LIMIT 1
            """))
            
            row = result.first()
            if row:
                print(f"FOUND in database!")
                print(f"ID: {row[0]}")
                print(f"Status: {row[1]}")
                print(f"Selected Model: {row[2]}")
                print(f"Created: {row[3]}")
            else:
                print(f"NOT FOUND: {test_exec_id}")
                
                # Show all recent executions
                print("\nRecent executions:")
                result = await conn.execute(text("""
                    SELECT id, execution_status, selected_model, created_at
                    FROM workflow_executions
                    ORDER BY created_at DESC
                    LIMIT 5
                """))
                
                for row in result:
                    print(f"{str(row[0])[:36]} | {row[1]} | Model: {row[2]}")
        
        await engine.dispose()
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
