#!/usr/bin/env python3
"""Check the latest workflow execution from our test"""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import json

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev"

async def main():
    try:
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            # Get THE LATEST execution (should be from our test)
            result = await conn.execute(text("""
                SELECT id, execution_status, selected_model, initial_input, phase_results, error_message, created_at
                FROM workflow_executions
                ORDER BY created_at DESC
                LIMIT 1
            """))
            
            row = result.first()
            if row:
                print("=" * 80)
                print("LATEST WORKFLOW EXECUTION")
                print("=" * 80)
                print(f"ID: {row[0]}")
                print(f"Status: {row[1]}")
                print(f"Selected Model: {row[2]}")
                print(f"Created: {row[6]}")
                print(f"\nInitial Input:")
                if row[3]:
                    print(json.dumps(row[3], indent=2)[:500])
                print(f"\nError Message:")
                if row[5]:
                    print(row[5][:300])
                print(f"\nPhase Results:")
                if row[4]:
                    try:
                        phase_json = json.loads(row[4]) if isinstance(row[4], str) else row[4]
                        print(json.dumps(phase_json, indent=2)[:800])
                    except:
                        print(row[4][:300])
            else:
                print("No executions found!")
        
        await engine.dispose()
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
