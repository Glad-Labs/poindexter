#!/usr/bin/env python3
"""Quick test script for task creation"""
import httpx
import asyncio
import json
import sys

async def main():
    # Get a token first
    auth_response = await httpx.AsyncClient().post(
        "http://localhost:8000/api/auth/github/callback",
        json={"code": "mock_auth_code_dev", "state": "test"}
    )
    
    token_data = auth_response.json()
    token = token_data["token"]
    print(f"[OK] Got token: {token[:20]}...")
    
    # Create a task
    task_data = {
        "task_name": "Test Blog Post",
        "topic": "Artificial Intelligence in Healthcare",
        "primary_keyword": "AI healthcare",
        "target_audience": "Healthcare professionals",
        "category": "Technology",
        "style": "professional",
        "tone": "informative",
        "target_length": "1500-2000"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/tasks",
            json=task_data,
            headers={"Authorization": f"Bearer {token}"}
        )
    
    result = response.json()
    task_id = result.get('task_id') or result.get('id')
    print(f"[OK] Task created: {task_id}")
    print(json.dumps(result, indent=2))
    
    # Wait for task execution (longer wait for actual generation)
    print("[INFO] Waiting for task execution...")
    for i in range(12):  # Wait up to 60 seconds
        await asyncio.sleep(5)
        
        # Get the task result
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:8000/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"}
            )
        
        task_result = response.json()
        status = task_result.get('status') or task_result.get('task_status')
        print(f"   Status: {status}")
        
        if status in ['completed', 'failed']:
            break
    else:
        task_result = response.json()
    
    task_result = response.json()
    print(f"\n[OK] Task result:")
    print(json.dumps(task_result, indent=2))
    
    # Extract content fields
    content = task_result.get('content') or task_result.get('generated_content')
    if content:
        content_len = len(content)
        print(f"\n[OK] Generated content length: {content_len} chars")
        print(f"   Preview: {content[:100]}...")
    else:
        print(f"\n[ERROR] No content generated")
        if task_result.get('orchestrator_error'):
            print(f"   Error: {task_result['orchestrator_error']}")
        if task_result.get('metadata'):
            print(f"   Metadata: {json.dumps(task_result['metadata'], indent=2)}")

if __name__ == "__main__":
    asyncio.run(main())
