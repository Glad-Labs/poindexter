#!/usr/bin/env python3
"""Simpler test - synchronous"""
import requests
import json

BASE_URL = "http://localhost:8000/api"

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
        "content": "# Full Pipeline Test\n\nThis is a test post created through the full pipeline."
    }
}

try:
    resp = requests.post(f"{BASE_URL}/tasks", json=task_payload, timeout=10)
    if resp.status_code == 201:
        task_data = resp.json()
        task_id = task_data.get('id')
        print(f"✅ Task created")
        print(f"   Task ID: {task_id}")
        print(f"   Status: {task_data.get('status')}")
        
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
        
        resp2 = requests.patch(f"{BASE_URL}/tasks/{task_id}", json=update_payload, timeout=10)
        if resp2.status_code == 200:
            updated = resp2.json()
            result = updated.get('result')
            print(f"✅ Task marked completed")
            print(f"   Status: {updated.get('status')}")
            print(f"   Result persisted: {bool(result)}")
            if result:
                print(f"     - Title: {result.get('title')}")
                print(f"     - Content length: {len(result.get('content', ''))}")
            
            # Step 3: Publish
            print(f"\n✋ Step 2: Publish Task to PostgreSQL")
            resp3 = requests.post(f"{BASE_URL}/tasks/{task_id}/publish", timeout=10)
            if resp3.status_code == 200:
                published = resp3.json()
                print(f"✅ Task published successfully!")
                print(f"   Message: {published.get('message')}")
                print(f"   Post ID: {published.get('post_id')}")
                print("\n✅ FULL PIPELINE TEST PASSED!")
            else:
                print(f"❌ Publish failed: {resp3.status_code}")
                print(f"   Response: {resp3.text}")
        else:
            print(f"❌ Update failed: {resp2.status_code}")
            print(f"   Response: {resp2.text}")
    else:
        print(f"❌ Task creation failed: {resp.status_code}")
        print(f"   Response: {resp.text}")
        
except requests.exceptions.ConnectionError as e:
    print(f"❌ CONNECTION ERROR: Cannot connect to {BASE_URL}")
    print(f"   Error: {e}")
except Exception as e:
    print(f"❌ ERROR: {e}")
