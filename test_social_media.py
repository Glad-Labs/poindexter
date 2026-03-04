#!/usr/bin/env python3
"""Test workflow with social_media template (no research phase)"""

import asyncio
import json
import requests

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

async def test():
    # Use social_media template which only has draft, assess, publish
    # and doesn't require research_agent
    payload = {
        "prompt": "Write a compelling tweet about the latest AI breakthroughs in healthcare",
        "tone": "professional",
        "model": "ollama-mistral"
    }
    
    print("Testing social_media workflow (no research agent required)")
    print(f"Payload keys: {list(payload.keys())}")
    print()
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/workflows/execute/social_media",
            json=payload,
            headers=HEADERS,
            timeout=120
        )
        
        print(f"Status: {resp.status_code}")
        data = resp.json()
        print(f"Execution ID: {data.get('execution_id')}")
        print(f"Status: {data.get('status')}")
        
        if data.get('phase_results'):
            print(f"\nPhase results:")
            for phase_name, result in data['phase_results'].items():
                status = result.get('status', 'unknown')
                print(f"  {phase_name}: {status}")
                if result.get('error'):
                    print(f"    Error: {result.get('error')[:150]}")
        
        if data.get('error_message'):
            print(f"\nError: {data['error_message'][:300]}")
        
        # Check database
        print("\n" + "=" * 80)
        print("Checking database...")
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        
        DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev"
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            result = await conn.execute(text(f"""
                SELECT execution_status, selected_model
                FROM workflow_executions  
                WHERE id = '{data.get('execution_id')}'
            """))
            row = result.first()
            if row:
                print(f"Database entry found!")
                print(f"  Status: {row[0]}")
                print(f"  Selected Model: {row[1]}")
            else:
                print(f"NOT FOUND in database (may not have been saved yet)")
        
        await engine.dispose()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
