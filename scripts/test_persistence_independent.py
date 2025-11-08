#!/usr/bin/env python3
"""
Independent test script for blog post task persistence.
This script runs without interfering with the backend process.
"""

import requests
import json
import time
import sys
from pathlib import Path

def test_blog_post_persistence():
    """Test blog post creation and persistence."""
    
    API_URL = "http://127.0.0.1:8000"
    
    print("=" * 80)
    print("🧪 BLOG POST PERSISTENCE TEST")
    print("=" * 80)
    print()
    
    # Test 1: Check if backend is running
    print("1️⃣  Checking if backend is running...")
    try:
        health_response = requests.get(f"{API_URL}/api/health", timeout=5)
        print(f"   ✅ Backend is running: {health_response.status_code}")
    except Exception as e:
        print(f"   ❌ Backend is NOT running: {e}")
        return False
    
    print()
    
    # Test 2: Create a blog post task
    print("2️⃣  Creating blog post task...")
    blog_post_data = {
        "topic": "Testing Task Persistence",
        "style": "technical",
        "tone": "professional",
        "target_length": 1500,
        "tags": ["test", "persistence", "logging"]
    }
    
    try:
        create_response = requests.post(
            f"{API_URL}/api/content/blog-posts",
            json=blog_post_data,
            timeout=10
        )
        print(f"   Status Code: {create_response.status_code}")
        print(f"   Response: {create_response.text[:200]}")
        
        if create_response.status_code == 201:
            task_data = create_response.json()
            task_id = task_data.get("task_id")
            print(f"   ✅ Task created with ID: {task_id}")
        else:
            print(f"   ❌ Failed to create task")
            return False
    except Exception as e:
        print(f"   ❌ Error creating task: {e}")
        return False
    
    print()
    
    # Test 3: Wait a moment for database to write
    print("3️⃣  Waiting for database write...")
    time.sleep(2)
    
    # Test 4: Check if database file exists
    print("4️⃣  Checking if database file was created...")
    db_path = Path(".tmp/content_tasks.db")
    if db_path.exists():
        db_size = db_path.stat().st_size
        print(f"   ✅ Database file exists: {db_path} ({db_size} bytes)")
    else:
        print(f"   ❌ Database file NOT found at: {db_path}")
    
    print()
    
    # Test 5: Retrieve task by ID
    print(f"5️⃣  Retrieving task {task_id}...")
    try:
        retrieve_response = requests.get(
            f"{API_URL}/api/content/blog-posts/tasks/{task_id}",
            timeout=10
        )
        print(f"   Status Code: {retrieve_response.status_code}")
        
        if retrieve_response.status_code == 200:
            task_info = retrieve_response.json()
            print(f"   ✅ Task found in database!")
            print(f"      - Status: {task_info.get('status')}")
            print(f"      - Progress: {task_info.get('progress', 'N/A')}")
            print(f"      - Created: {task_info.get('created_at', 'N/A')}")
        else:
            print(f"   ⚠️  Task not found or error: {retrieve_response.text}")
    except Exception as e:
        print(f"   ❌ Error retrieving task: {e}")
    
    print()
    
    # Test 6: List all tasks
    print("6️⃣  Listing all tasks...")
    try:
        list_response = requests.get(
            f"{API_URL}/api/tasks",
            timeout=10
        )
        
        if list_response.status_code == 200:
            tasks = list_response.json()
            if isinstance(tasks, list):
                print(f"   ✅ Total tasks in database: {len(tasks)}")
                for idx, task in enumerate(tasks[-3:], 1):  # Show last 3
                    print(f"      Task {idx}: {task.get('task_id', 'N/A')} - Status: {task.get('status', 'N/A')}")
            else:
                print(f"   Response format: {type(tasks)}")
                print(f"   {list_response.text[:200]}")
        else:
            print(f"   ⚠️  Could not list tasks: {list_response.status_code}")
    except Exception as e:
        print(f"   ❌ Error listing tasks: {e}")
    
    print()
    print("=" * 80)
    print("✅ TEST COMPLETE - Check backend logs for 🟢 and ✅✅ emoji messages")
    print("=" * 80)
    
    return True

if __name__ == "__main__":
    try:
        success = test_blog_post_persistence()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n❌ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
