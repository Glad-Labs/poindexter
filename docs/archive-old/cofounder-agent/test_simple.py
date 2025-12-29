#!/usr/bin/env python3
"""Direct test - doesn't use FastAPI app directly"""
import asyncio
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

async def main():
    import aiohttp
    
    BASE_URL = "http://localhost:8000/api"
    
    print("\n" + "="*70)
    print("🚀 FULL PIPELINE TEST: Create → Publish → Verify")
    print("="*70)
    
    async with aiohttp.ClientSession() as session:
        # Step 1: Create a task
        print("\n✋ Step 1: Create Task via API")
        task_payload = {
            "task_name": "Full Pipeline Test",
            "topic": "Full Pipeline Test - Blog Post",
            "primary_keyword": "pipeline",
            "target_audience": "developers",
            "category": "testing",
            "metadata": {
                "content": "# Full Pipeline Test\n\nThis is a test post created through the full pipeline."
            }
        }
        
        try:
            async with session.post(f"{BASE_URL}/tasks", json=task_payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 201:
                    task_data = await resp.json()
                    task_id = task_data.get('id')
                    print(f"✅ Task created - ID: {task_id}")
                    
                    # Step 2: Update task with result
                    print(f"\n✋ Step 1.5: Mark Task as Completed with Result")
                    update_payload = {
                        "status": "completed",
                        "result": {
                            "title": "Full Pipeline Test Post",
                            "content": task_payload["metadata"]["content"],
                            "excerpt": "Test post from pipeline"
                        }
                    }
                    
                    async with session.patch(f"{BASE_URL}/tasks/{task_id}", json=update_payload, timeout=aiohttp.ClientTimeout(total=10)) as resp2:
                        if resp2.status == 200:
                            updated = await resp2.json()
                            result = updated.get('result')
                            print(f"✅ Task marked completed")
                            print(f"   Status: {updated.get('status')}")
                            print(f"   Result: {result}")
                            
                            # Step 3: Publish
                            print(f"\n✋ Step 2: Publish Task to PostgreSQL")
                            async with session.post(f"{BASE_URL}/tasks/{task_id}/publish", timeout=aiohttp.ClientTimeout(total=10)) as resp3:
                                if resp3.status == 200:
                                    published = await resp3.json()
                                    print(f"✅ Task published")
                                    print(f"   Message: {published.get('message')}")
                                    print(f"   Post ID: {published.get('post_id')}")
                                else:
                                    text = await resp3.text()
                                    print(f"❌ Publish failed: {resp3.status}")
                                    print(f"   Response: {text}")
                        else:
                            text = await resp2.text()
                            print(f"❌ Update failed: {resp2.status}")
                            print(f"   Response: {text}")
                else:
                    text = await resp.text()
                    print(f"❌ Task creation failed: {resp.status}")
                    print(f"   Response: {text}")
        except asyncio.TimeoutError:
            print("❌ TIMEOUT - Backend not responding on http://localhost:8000")
        except ConnectionRefusedError as e:
            print(f"❌ CONNECTION REFUSED: {e}")
        except Exception as e:
            print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
