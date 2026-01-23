#!/usr/bin/env python3
"""
Test script for LLM-based title generation in blog post creation
"""

import asyncio
import httpx
import json
from datetime import datetime

async def test_title_generation():
    """Test creating a blog post and verify title generation"""
    
    BASE_URL = "http://localhost:8000"
    
    # Create a blog post task
    task_data = {
        "request_type": "content_generation",
        "task_type": "blog_post",
        "topic": "The Future of Artificial Intelligence in Healthcare",
        "style": "technical",
        "tone": "professional",
        "target_length": 1500,
        "tags": ["AI", "Healthcare", "Technology"],
        "generate_featured_image": True
    }
    
    print("📝 Creating blog post task...")
    print(json.dumps(task_data, indent=2))
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Create the task with Authorization header
            response = await client.post(
                f"{BASE_URL}/api/tasks",
                json=task_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-token-for-development"  # Development auth bypass
                }
            )
            
            if response.status_code != 201:
                print(f"❌ Failed to create task: {response.status_code}")
                print(response.text)
                return
            
            task = response.json()
            task_id = task.get("task_id") or task.get("id")
            
            print(f"\n✅ Task created successfully!")
            print(f"   Task ID: {task_id}")
            print(f"   Status: {task.get('status')}")
            print()
            
            # Poll for task completion (max 2 minutes)
            max_attempts = 24  # 2 minutes with 5-second intervals
            attempt = 0
            
            while attempt < max_attempts:
                # Get task details
                response = await client.get(f"{BASE_URL}/api/tasks/{task_id}")
                
                if response.status_code != 200:
                    print(f"❌ Failed to get task: {response.status_code}")
                    return
                
                task_details = response.json()
                status = task_details.get("status")
                title = task_details.get("title")
                
                print(f"[{attempt + 1}/{max_attempts}] Status: {status}")
                if title:
                    print(f"                 Title: {title}")
                
                if status in ["completed", "published", "approved"]:
                    print(f"\n✅ Task completed!")
                    print(f"\n📋 Task Details:")
                    print(f"   Task ID: {task_id}")
                    print(f"   Status: {status}")
                    print(f"   Title: {title or 'Not generated'}")
                    print(f"   Content Length: {len(task_details.get('content', ''))} chars")
                    print(f"   Model Used: {task_details.get('model_used')}")
                    
                    if title:
                        print(f"\n✅ Title generation successful!")
                        print(f"   Generated Title: {title}")
                    else:
                        print(f"\n⚠️ Title was not generated")
                    
                    return
                
                if status == "failed":
                    print(f"\n❌ Task failed!")
                    print(f"   Error: {task_details.get('error_message')}")
                    return
                
                # Wait before next poll
                await asyncio.sleep(5)
                attempt += 1
            
            print(f"\n⏱️ Task did not complete within 2 minutes")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    print("🧪 Testing LLM-based Title Generation\n")
    asyncio.run(test_title_generation())
