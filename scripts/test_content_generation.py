#!/usr/bin/env python3
"""Quick test to create a task and see if background processing works"""
import asyncio
import aiohttp
import json

async def test_task_creation():
    """Create a task and monitor its progress"""
    
    api_url = "http://localhost:8000"
    
    # Create task
    print("=" * 80)
    print("Creating test task...")
    print("=" * 80)
    
    task_payload = {
        "task_name": "Test Blog Post",
        "topic": "The Future of Artificial Intelligence",
        "primary_keyword": "AI trends 2025",
        "target_audience": "Tech professionals",
        "category": "technology"
    }
    
    async with aiohttp.ClientSession() as session:
        # Create task
        async with session.post(f"{api_url}/api/tasks", json=task_payload) as resp:
            if resp.status == 201:
                result = await resp.json()
                task_id = result['id']
                print(f"✅ Task created successfully!")
                print(f"   Task ID: {task_id}")
                print(f"   Status: {result['status']}")
                print(f"   Created At: {result['created_at']}")
            else:
                print(f"❌ Failed to create task: {resp.status}")
                print(await resp.text())
                return
        
        # Wait a moment for background processing
        print("\n⏳ Waiting 15 seconds for background content generation...")
        await asyncio.sleep(15)
        
        # Check task status and content
        print("\n📖 Checking task status...")
        async with session.get(f"{api_url}/api/tasks/{task_id}") as resp:
            if resp.status == 200:
                task_data = await resp.json()
                print(f"✅ Task retrieved!")
                print(f"   Status: {task_data['status']}")
                print(f"   Updated At: {task_data['updated_at']}")
                
                # Check result
                if task_data.get('result'):
                    try:
                        if isinstance(task_data['result'], str):
                            result = json.loads(task_data['result'])
                        else:
                            result = task_data['result']
                        
                        print(f"\n📝 Generated Content:")
                        print("   " + "=" * 70)
                        if 'content' in result:
                            content = result['content']
                            # Print first 500 chars
                            preview = content[:500] if len(content) > 500 else content
                            print(preview)
                            if len(content) > 500:
                                print(f"\n   ... ({len(content) - 500} more characters)")
                            print("   " + "=" * 70)
                            print(f"\n✅ SUCCESS! Generated {len(content)} characters of blog content!")
                        else:
                            print(f"   Result keys: {list(result.keys())}")
                    except json.JSONDecodeError:
                        print(f"   Raw result: {task_data['result']}")
                else:
                    print(f"⚠️ No result yet - task may still be processing")
                    print(f"   Result: {task_data.get('result')}")
            else:
                print(f"❌ Failed to retrieve task: {resp.status}")

if __name__ == "__main__":
    asyncio.run(test_task_creation())
