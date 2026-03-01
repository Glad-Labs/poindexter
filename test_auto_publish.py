#!/usr/bin/env python3
import requests
import json

headers = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

# Get a task in awaiting_approval status
print("[1] Fetching task in awaiting_approval status...")
resp = requests.get(
    "http://localhost:8000/api/tasks?status=awaiting_approval&limit=1",
    headers=headers,
    timeout=10
)

if resp.status_code != 200:
    print(f"[ERROR] Failed to fetch tasks: {resp.status_code}")
    exit(1)

tasks = resp.json().get("tasks", [])
if not tasks:
    print("[ERROR] No tasks in awaiting_approval status")
    exit(1)

task = tasks[0]
task_id = task.get("id") or task.get("task_id")
print(f"[OK] Found task: {task_id}")
print(f"     Topic: {task.get('topic')}")
print(f"     Current status: {task.get('status')}")

# Test the approval with auto_publish=true
print("\n[2] Calling /approve endpoint with auto_publish=true...")
approve_resp = requests.post(
    f"http://localhost:8000/api/tasks/{task_id}/approve",
    headers=headers,
    json={
        "approved": True,
        "auto_publish": True,
        "human_feedback": "Looks good, auto-publishing!"
    },
    timeout=30
)

print(f"[INFO] Response status: {approve_resp.status_code}")
if approve_resp.status_code == 200:
    result = approve_resp.json()
    print(f"[INFO] Response body:")
    print(json.dumps(result, indent=2, default=str))
    print(f"\n[INFO] Response task status: {result.get('status')}")
    post_id = result.get('post_id')
    post_slug = result.get('post_slug')
    print(f"[INFO] post_id: {post_id}")
    print(f"[INFO] post_slug: {post_slug}")
else:
    print(f"[ERROR] {approve_resp.status_code}")
    print(approve_resp.text)
