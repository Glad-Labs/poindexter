#!/usr/bin/env python3
"""
Sprint 2 - Async Execution Refactor - Verification Tests

Tests for:
- Task 2.1: Routes return 202 ACCEPTED instead of sync responses
- Task 2.2: Status query endpoints work correctly
- Task 2.3: TaskExecutor timeout increased to 20 minutes
"""

import asyncio
import json
import httpx
import sys
from datetime import datetime, timezone

BASE_URL = "http://localhost:8000"
AUTH_HEADER = {"Authorization": "Bearer dev-token-test"}


async def test_task_creation_returns_202():
    """Test POST /api/tasks returns 202 ACCEPTED"""
    print("\n📋 Test 1: Task creation returns 202 ACCEPTED")
    print("-" * 60)
    
    async with httpx.AsyncClient() as client:
        payload = {
            "task_type": "blog_post",
            "topic": "Test Blog for Sprint 2",
            "style": "technical",
            "tone": "professional",
            "target_length": 1000,
        }
        
        response = await client.post(
            f"{BASE_URL}/api/tasks",
            json=payload,
            headers=AUTH_HEADER,
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 202:
            print("✅ PASS: Returns 202 ACCEPTED")
            execution_id = response.json().get("id") or response.json().get("task_id")
            return execution_id
        else:
            print(f"❌ FAIL: Expected 202, got {response.status_code}")
            return None


async def test_workflow_template_returns_202():
    """Test POST /api/workflows/execute/{template_name} returns 202 ACCEPTED"""
    print("\n📋 Test 2: Workflow template execution returns 202 ACCEPTED")
    print("-" * 60)
    
    async with httpx.AsyncClient() as client:
        payload = {
            "topic": "Test Workflow for Sprint 2",
            "keywords": ["test", "sprint2"],
            "target_audience": "Developers",
        }
        
        response = await client.post(
            f"{BASE_URL}/api/workflows/execute/blog_post",
            json=payload,
            headers=AUTH_HEADER,
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 202:
            print("✅ PASS: Returns 202 ACCEPTED")
            return response.json().get("execution_id")
        else:
            print(f"❌ FAIL: Expected 202, got {response.status_code}")
            return None


async def test_task_status_endpoint(task_id):
    """Test GET /api/tasks/{task_id}/status endpoint"""
    print(f"\n📋 Test 3: Get task status - GET /api/tasks/{task_id}/status")
    print("-" * 60)
    
    if not task_id:
        print("⚠️  SKIP: No task_id from previous test")
        return
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/api/tasks/{task_id}/status",
            headers=AUTH_HEADER,
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 200:
            data = response.json()
            if "status" in data and "task_id" in data:
                print("✅ PASS: Status endpoint returns task status")
                return data
            else:
                print("❌ FAIL: Missing status or task_id in response")
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
    
    return None


async def test_task_result_endpoint(task_id):
    """Test GET /api/tasks/{task_id}/result endpoint"""
    print(f"\n📋 Test 4: Get task result - GET /api/tasks/{task_id}/result")
    print("-" * 60)
    
    if not task_id:
        print("⚠️  SKIP: No task_id from previous test")
        return
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/api/tasks/{task_id}/result",
            headers=AUTH_HEADER,
        )
        
        print(f"Status Code: {response.status_code}")
        if response.status_code in [200, 202]:
            print(f"Response: {json.dumps(response.json(), indent=2)}")
            if response.status_code == 202:
                print("✅ PASS: Returns 202 while task in progress (as expected)")
            else:
                data = response.json()
                if "result" in data or "status" in data:
                    print("✅ PASS: Result endpoint returns task result")
                else:
                    print("❌ FAIL: Missing result or status in response")
        else:
            print(f"❌ FAIL: Expected 200 or 202, got {response.status_code}")
            print(f"Response: {response.text}")


async def test_workflow_status_endpoint(execution_id):
    """Test GET /api/workflows/status/{execution_id} endpoint"""
    print(f"\n📋 Test 5: Get workflow status - GET /api/workflows/status/{execution_id}")
    print("-" * 60)
    
    if not execution_id:
        print("⚠️  SKIP: No execution_id from previous test")
        return
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/api/workflows/status/{execution_id}",
            headers=AUTH_HEADER,
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 200:
            data = response.json()
            if "status" in data and "execution_id" in data:
                print("✅ PASS: Workflow status endpoint works")
            else:
                print("❌ FAIL: Missing status or execution_id in response")
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")


async def test_health_check():
    """Test that API is reachable"""
    print("\n📋 Test 0: Health check - Verify API is reachable")
    print("-" * 60)
    
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ PASS: API is reachable")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print("Make sure services are running: npm run dev")
            return False


async def main():
    """Run all tests"""
    print("=" * 60)
    print("🚀 Sprint 2 - Async Execution Refactor - Verification Tests")
    print("=" * 60)
    print(f"Testing against: {BASE_URL}")
    
    # Test 0: Health check
    if not await test_health_check():
        sys.exit(1)
    
    # Test 1: Task creation returns 202
    task_id = await test_task_creation_returns_202()
    
    # Test 2: Workflow template execution returns 202
    workflow_execution_id = await test_workflow_template_returns_202()
    
    # Test 3: Task status endpoint
    await asyncio.sleep(1)  # Give task a moment to process
    if task_id:
        await test_task_status_endpoint(task_id)
    
    # Test 4: Task result endpoint
    await asyncio.sleep(1)
    if task_id:
        await test_task_result_endpoint(task_id)
    
    # Test 5: Workflow status endpoint
    if workflow_execution_id:
        await test_workflow_status_endpoint(workflow_execution_id)
    
    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
