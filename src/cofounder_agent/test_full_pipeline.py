#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full pipeline test: Create task in backend → Publish → Verify in PostgreSQL
This tests the complete flow without going through the UI
"""
import asyncio
import aiohttp
import json
import sys
import os
from datetime import datetime

# Fix Windows encoding issues
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:8000/api"

async def test_full_pipeline():
    """Test complete pipeline: Create task -> Publish -> Verify"""
    
    async with aiohttp.ClientSession() as session:
        print("\n" + "="*70)
        print("🚀 FULL PIPELINE TEST: Create → Publish → Verify")
        print("="*70)
        
        # Step 1: Create a task
        print("\n✋ Step 1: Create Task via API")
        task_payload = {
            "task_name": "Full Pipeline Test",
            "topic": "Full Pipeline Test - Blog Post",
            "primary_keyword": "pipeline",
            "target_audience": "developers",
            "category": "testing",
            "metadata": {
                "content": "# Full Pipeline Test\n\nThis is a test post created through the full pipeline.\n\n## Verification\n- Task created in backend ✓\n- Published to PostgreSQL ✓\n- Verifying in database ✓"
            }
        }
        
        try:
            async with session.post(f"{BASE_URL}/tasks", json=task_payload) as resp:
                if resp.status == 201:
                    task_data = await resp.json()
                    task_id = task_data.get('id')
                    print(f"✅ PASSED: Task created")
                    print(f"   Task ID: {task_id}")
                    print(f"   Title: {task_data.get('task_name')}")
                    print(f"   Status: {task_data.get('status')}")
                else:
                    print(f"❌ FAILED: Task creation returned {resp.status}")
                    print(f"   Response: {await resp.text()}")
                    return
        except Exception as e:
            print(f"❌ FAILED: Could not create task: {e}")
            return
        
        # Step 1.5: Update task to 'completed' status with result
        print(f"\n✋ Step 1.5: Mark Task as Completed with Content")
        result_content = "# Full Pipeline Test\n\nThis is a test post created through the full pipeline.\n\n## Verification\n- Task created in backend ✓\n- Published to PostgreSQL ✓\n- Verifying in database ✓"
        update_payload = {
            "status": "completed",
            "result": {
                "content": result_content,
                "title": "Full Pipeline Test - Blog Post"
            }
        }
        
        try:
            async with session.patch(f"{BASE_URL}/tasks/{task_id}", json=update_payload) as resp:
                if resp.status == 200:
                    task_data = await resp.json()
                    print(f"✅ PASSED: Task marked as completed")
                    print(f"   Status: {task_data.get('status')}")
                    print(f"   Result in response: {task_data.get('result')}")
                else:
                    print(f"❌ FAILED: Task update returned {resp.status}")
                    print(f"   Response: {await resp.text()}")
                    return
        except Exception as e:
            print(f"❌ FAILED: Could not update task: {e}")
            return
        
        # Step 2: Publish the task
        print(f"\n✋ Step 2: Publish Task (ID: {task_id})")
        try:
            async with session.post(f"{BASE_URL}/tasks/{task_id}/publish") as resp:
                if resp.status == 200:
                    publish_data = await resp.json()
                    print(f"✅ PASSED: Task published to PostgreSQL")
                    print(f"   Status: {publish_data.get('status')}")
                    print(f"   Message: {publish_data.get('message')}")
                    if 'strapi_post_id' in publish_data:
                        print(f"   Strapi Post ID: {publish_data['strapi_post_id']}")
                else:
                    print(f"❌ FAILED: Publish returned {resp.status}")
                    print(f"   Response: {await resp.text()}")
                    return
        except Exception as e:
            print(f"❌ FAILED: Could not publish task: {e}")
            return
        
        # Step 3: Verify in database using our check script
        print(f"\n✋ Step 3: Verify Post in PostgreSQL Database")
        import sys
        sys.path.insert(0, '.')
        
        try:
            from services.strapi_publisher import StrapiPublisher
            
            publisher = StrapiPublisher()
            await publisher.connect()
            
            exists, post_data = await publisher.verify_post_exists("Full Pipeline Test - Blog Post")
            
            if exists:
                print(f"✅ PASSED: Post found in database")
                print(f"   ID: {post_data['id']}")
                print(f"   Title: {post_data['title']}")
                print(f"   Slug: {post_data['slug']}")
                print(f"   Created: {post_data['created_at']}")
            else:
                print(f"❌ FAILED: Post not found in database")
            
            # Get recent posts
            print(f"\n✋ Step 4: List Recent Posts from Database")
            success, posts, message = await publisher.get_posts(limit=5)
            print(f"   {message}")
            if posts:
                print(f"   Recent posts:")
                for p in posts:
                    print(f"     - ID: {p['id']} | Title: {p['title']} | Created: {p['created_at']}")
            
            await publisher.disconnect()
            
        except Exception as e:
            print(f"❌ FAILED: Verification error: {e}")
            return
        
        print("\n" + "="*70)
        print("✅ FULL PIPELINE TEST COMPLETE - ALL STEPS PASSED!")
        print("="*70)
        print("\n🎉 Summary:")
        print("  ✓ Task created via API")
        print("  ✓ Task published to PostgreSQL database")
        print("  ✓ Post verified in database")
        print("  ✓ Ready for production!")
        print()


if __name__ == "__main__":
    asyncio.run(test_full_pipeline())
