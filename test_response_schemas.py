#!/usr/bin/env python3
"""Test response schemas for consistency"""
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

print("[TEST] Response Schema Consistency")
print("="*80)

# Get tasks and check response format
print("\n[1] GET /api/tasks response format")
resp = requests.get(f"{BASE_URL}/api/tasks?limit=1", headers=HEADERS)
data = resp.json()
print(f"Root keys: {list(data.keys())}")
if "tasks" in data:
    task = data["tasks"][0]
    print(f"Task keys: {list(task.keys())[:10]}...")
else:
    print("[BUG] Expected 'tasks' key in response")

# Get posts and check response format
print("\n[2] GET /api/posts response format")
resp = requests.get(f"{BASE_URL}/api/posts?limit=1", headers=HEADERS)
data = resp.json()
print(f"Root keys: {list(data.keys())}")
if "data" in data:
    if data["data"]:
        post = data["data"][0]
        print(f"Post has 'id': {'id' in post}")
        print(f"Post has 'slug': {'slug' in post}")
        print(f"Post has 'content': {'content' in post}")
        print(f"Post has 'featured_image_url': {'featured_image_url' in post}")
else:
    print("[BUG] Expected 'data' key in response")

# Check approval response format
print("\n[3] POST /api/tasks/{id}/approve response format")
resp = requests.get(f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1", headers=HEADERS)
tasks = resp.json().get("tasks", [])
if tasks:
    task_id = tasks[0].get('id') or tasks[0].get('task_id')
    resp = requests.post(
        f"{BASE_URL}/api/tasks/{task_id}/approve",
        headers=HEADERS,
        json={"approved": True, "auto_publish": True}
    )
    if resp.status_code == 200:
        result = resp.json()
        print(f"Approval response keys: {list(result.keys())}")
        print(f"Has 'status': {'status' in result}")
        print(f"Has 'post_id': {'post_id' in result}")
        print(f"Has 'post_slug': {'post_slug' in result}")
        
        if result.get('status') == 'published':
            if not result.get('post_id'):
                print("[BUG] status is 'published' but post_id is null")
    else:
        print(f"[ERROR] {resp.status_code}")

print("\n" + "="*80)
