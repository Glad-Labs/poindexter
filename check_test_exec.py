#!/usr/bin/env python3
"""Check for the test execution by ID"""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import json

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev"

test_exec_ids = [
    "ceef98cb-08ec-4722-8bf0-b3ba1f2a7548",  # From current test
    "1d569b7f-d481-457f-9683-0984467c9895",  # From another test
]

async def main():
    try:
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            for test_id in test_exec_ids:
                print(f"\n{'=' * 80}")
                print(f"Looking for execution: {test_id}")
                print('=' * 80)
                
                result = await conn.execute(text(f"""
                    SELECT id, execution_status, selected_model, created_at 
                    FROM workflow_executions
                    WHERE id = '{test_id}'
                """))
                
                row = result.first()
                if row:
                    print(f"FOUND!")
                    print(f"Status: {row[1]}")
                    print(f"Selected Model: {row[2]}")
                    print(f"Created: {row[3]}")
                else:
                    print(f"NOT FOUND in database")
            
            # Also get execution IDs that look like UUIDs updated very recently
            print(f"\n{'=' * 80}")
            print("All executions from last 5 minutes:")
            print('=' * 80)
            result = await conn.execute(text("""
                SELECT id, execution_status, selected_model, created_at
                FROM workflow_executions
                WHERE created_at > NOW() - INTERVAL '5 minutes'
                ORDER BY created_at DESC
                LIMIT 10
            """))
            
            for row in result:
                print(f"{str(row[0])[:36]} | Status: {row[1]:12} | Model: {str(row[2]):20} | {row[3]}")
        
        await engine.dispose()
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
