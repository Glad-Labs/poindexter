#!/usr/bin/env python3
"""
Final test of model persistence feature.

This script:
1. Executes a workflow with a model parameter
2. Extracts the execution ID from response
3. Queries database to verify model was persisted
"""
import asyncio
import httpx
import asyncpg
import os
import time

async def test_model_persistence():
    print("=" * 70)
    print("TESTING: Model Selection Feature - End-to-End")
    print("=" * 70)
    
    # Get database config
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/glad_labs_dev")
    
    # API request parameters
    api_url = "http://localhost:8000/api/workflows/execute/social_media"
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json"
    }
    payload = {
        "prompt": "Write a social media post about AI and climate action",
        "model": "gpt-4-turbo"
    }
    
    try:
        # Step 1: Execute workflow via API
        print("\n[Step 1] Executing workflow with model parameter...")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            result = resp.json()
        
        execution_id = result.get('execution_id')
        workflow_id = result.get('workflow_id')
        
        print(f"  ✓ API Response Status: {resp.status_code}")
        print(f"  ✓ Execution ID: {execution_id}")
        print(f"  ✓ Workflow ID: {workflow_id}")
        
        if not execution_id:
            print("  ✗ ERROR: No execution_id in response")
            print(f"  Response: {result}")
            return False
        
        # Give database a moment to persist
        await asyncio.sleep(2)
        
        # Step 2: Query database for execution
        print("\n[Step 2] Checking database for persisted execution...")
        conn = await asyncpg.connect(db_url)
        
        execution_row = await conn.fetchrow(
            "SELECT id, selected_model, execution_status, workflow_id FROM workflow_executions WHERE id=$1",
            execution_id
        )
        
        if not execution_row:
            print("  ✗ ERROR: Execution not found in database!")
            await conn.close()
            return False
        
        print(f"  ✓ Execution found in database")
        print(f"    - ID: {execution_row['id']}")
        print(f"    - Selected Model: {execution_row['selected_model']}")
        print(f"    - Workflow ID: {execution_row['workflow_id']}")
        print(f"    - Status: {execution_row['execution_status']}")
        
        # Step 3: Verify model value
        print("\n[Step 3] Verifying model parameter persistence...")
        expected_model = payload["model"]
        actual_model = execution_row['selected_model']
        
        if actual_model == expected_model:
            print(f"  ✓✓✓ MODEL PERSISTENCE WORKING! ✓✓✓")
            print(f"    - Expected: {expected_model}")
            print(f"    - Actual:   {actual_model}")
        else:
            print(f"  ✗ ERROR: Model mismatch!")
            print(f"    - Expected: {expected_model}")
            print(f"    - Actual:   {actual_model}")
            await conn.close()
            return False
        
        # Step 4: Verify workflow exists
        print("\n[Step 4] Verifying workflow was persisted...")
        workflow_row = await conn.fetchrow(
            "SELECT id, name FROM custom_workflows WHERE id=$1",
            workflow_id
        )
        
        if workflow_row:
            print(f"  ✓ Workflow found in database")
            print(f"    - ID: {workflow_row['id']}")
            print(f"    - Name: {workflow_row['name']}")
        else:
            print(f"  ✗ ERROR: Workflow not found with ID {workflow_id}")
            await conn.close()
            return False
        
        await conn.close()
        
        # Final summary
        print("\n" + "=" * 70)
        print("✓✓✓ SUCCESS! Model persistence feature is working end-to-end!")
        print("=" * 70)
        print("\nFeature Summary:")
        print(f"  • Model parameter accepted in API request")
        print(f"  • Model value extracted and passed through workflow pipeline")
        print(f"  • Model persisted to workflow_executions.selected_model in database")
        print(f"  • Workflow template successfully persisted before execution")
        
        return True
        
    except Exception as e:
        print(f"\nERROR: {e}") 
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_model_persistence())
    exit(0 if success else 1)
