#!/usr/bin/env python
"""Test model selection persistence feature"""
import requests
import asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

HEADERS = {'Authorization': 'Bearer dev-token-123', 'Content-Type': 'application/json'}
DB_URL = 'postgresql+asyncpg://postgres:postgres@localhost:5432/glad_labs_dev'

async def check_db(exec_id):
    """Query database for execution record"""
    engine = create_async_engine(DB_URL, echo=False)
    async with engine.begin() as conn:
        result = await conn.execute(text(
            f"SELECT selected_model, execution_status FROM workflow_executions WHERE id = '{exec_id}'"
        ))
        row = result.first()
        return row
    await engine.dispose()

def test_model_persistence():
    """Test that model parameter is persisted to database"""
    print("\n=== Testing Model Selection Persistence ===\n")
    
    # Test 1: Execute with explicit model
    payload = {
        'prompt': 'Generate AI content',
        'model': 'gpt-4-turbo'
    }
    
    print(f"1. Sending request with model: {payload['model']}")
    resp = requests.post(
        'http://localhost:8000/api/workflows/execute/social_media',
        json=payload,
        headers=HEADERS,
        timeout=60
    )
    
    exec_id = resp.json().get('execution_id')
    print(f"   Execution ID: {exec_id}")
    print(f"   API Status: {resp.status_code}")
    
    # Wait and check database
    print("\n2. Checking database for persistence...")
    asyncio.run(asyncio.sleep(3))
    
    row = asyncio.run(check_db(exec_id))
    if row:
        model, status = row
        print(f"   ✓ Found in database")
        print(f"     Model: {model}")
        print(f"     Status: {status}")
        if model == 'gpt-4-turbo':
            print(f"\n✓ SUCCESS: Model parameter persisted correctly!")
            return True
        else:
            print(f"\n✗ FAILURE: Expected model 'gpt-4-turbo', got '{model}'")
            return False
    else:
        print(f"   ✗ Not found in database")
        print(f"\n✗ FAILURE: Execution not persisted")
        return False

if __name__ == '__main__':
    success = test_model_persistence()
    exit(0 if success else 1)
