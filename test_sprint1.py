#!/usr/bin/env python3
"""Test Sprint 1: Workflow persistence and WebSocket wiring"""

import asyncio
import json
import httpx
import uuid
import asyncpg

async def check_db_for_execution(execution_id):
    """Check database for the execution record"""
    try:
        conn = await asyncpg.connect(
            "postgresql://postgres:postgres@localhost/glad_labs_dev"
        )
        
        row = await conn.fetchrow(
            "SELECT id, owner_id, execution_status FROM workflow_executions WHERE id = $1",
            execution_id
        )
        
        if row:
            print(f"Found in database: Owner={row['owner_id']}, Status={row['execution_status']}")
            return row['owner_id']
        else:
            print("Not found in database")
            
        await conn.close()
    except Exception as e:
        print(f"DB Error: {e}")
    
    return None

async def test_workflow_persistence():
    """Test workflow execution and persistence"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Test 1: Execute a workflow
        print("TEST 1: Execute workflow...")
        
        url = "http://localhost:8000/api/workflows/execute/blog_post"
        headers = {"Authorization": "Bearer dev-token-test"}
        payload = {
            "input_data": {
                "topic": "Sprint 1 Test Execution",
                "keywords": "test, sprint1"
            }
        }
        
        try:
            response = await asyncio.wait_for(
                client.post(url, json=payload, headers=headers),
                timeout=8.0
            )
            print(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                exec_id = result.get("execution_id")
                print(f"SUCCESS: Workflow executed!")
                print(f"  Execution ID: {exec_id}")
                print(f"  Status: {result.get('status')}")
                
                # Check what's in the database
                print(f"\nChecking database for execution...")
                owner_id = await check_db_for_execution(exec_id)
                
                # Test 2: Retrieve execution from database
                print(f"\nTEST 2: Retrieve execution from API...")
                get_url = f"http://localhost:8000/api/workflows/executions/{exec_id}"
                
                response = await asyncio.wait_for(
                    client.get(get_url, headers=headers),
                    timeout=5.0
                )
                print(f"Response status: {response.status_code}")
                
                if response.status_code == 404 and owner_id:
                    print(f"HINT: Execution was saved with owner_id={owner_id}")
                    print(f"      But API request used owner_id={headers.get('Authorization', 'none')}")
                    print(f"  Consider testing direct DB query instead of API enforcement")
                
                if response.status_code == 200:
                    print(f"SUCCESS: Execution retrieved!")
                    execution = response.json()
                    print(f"  Status: {execution.get('execution_status')}")
                else:
                    print(f"FAIL: {response.status_code}")
                    print(f"  {response.json()}")
            else:
                print(f"FAIL: Workflow execution returned {response.status_code}")
                
        except asyncio.TimeoutError:
            print(f"TIMEOUT: Workflow execution took longer than 8 seconds")
        except Exception as e:
            print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_workflow_persistence())
