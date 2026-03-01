#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

print("[TEST] Auto-Publish Debug")
print("="*80)

# Get a task
resp = requests.get(
    f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1",
    headers=HEADERS
)

tasks = resp.json().get("tasks", [])
if not tasks:
    print("No tasks")
    exit(1)

task = tasks[0]
task_id = task.get('id') or task.get('task_id')
print(f"Task: {task_id[:8]}")
print(f"Has content: {'content' in task}")
print(f"Has featured_image_url: {'featured_image_url' in task}")

# Approve with auto_publish
print("\nApproving with auto_publish=true...")
resp = requests.post(
    f"{BASE_URL}/api/tasks/{task_id}/approve",
    headers=HEADERS,
    json={"approved": True, "auto_publish": True}
)

if resp.status_code != 200:
    print(f"[ERROR] {resp.status_code}")
    print(resp.text[:300])
    exit(1)

result = resp.json()
print(f"\nApproval response:")
print(f"  status: {result.get('status')}")
print(f"  post_id in response: {result.get('post_id')}")
print(f"  post_slug in response: {result.get('post_slug')}")

# Now check the task directly from database
print(f"\nFetching updated task from database...")
resp = requests.get(f"{BASE_URL}/api/tasks/{task_id}", headers=HEADERS)
task = resp.json()
print(f"Task status: {task.get('status')}")

task_result = task.get("result", {})
if isinstance(task_result, str):
    task_result = json.loads(task_result)

print(f"Result post_id: {task_result.get('post_id')}")
print(f"Result post_slug: {task_result.get('post_slug')}")

# Check if post was actually created
if task_result.get('post_id'):
    print(f"\nChecking if post exists in posts table...")
    post_id = task_result.get('post_id')
    resp = requests.get(f"{BASE_URL}/api/posts/{post_id}", headers=HEADERS)
    if resp.status_code == 200:
        print(f"[OK] Post found in database")
    else:
        print(f"[BUG] Post not found (status {resp.status_code})")

