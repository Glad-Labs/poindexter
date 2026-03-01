#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"  
HEADERS = {"Authorization": "Bearer dev-token-123"}

# Get ANY task in approved status
print("[STEP 1] Fetching recent approved task...")
resp = requests.get(f"{BASE_URL}/api/tasks?status=approved&limit=1", headers=HEADERS)
if resp.status_code != 200 or not resp.json().get("tasks"):
    print("Getting pending approval task...")
    resp = requests.get(f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=5", headers=HEADERS)
    if resp.status_code != 200 or not resp.json().get("tasks"):
        print("No tasks found")
        exit(1)

tasks = resp.json().get("tasks", [])
if not tasks:
    print("No tasks available")
    exit(1)

task = tasks[0]
task_id = task.get('id') or task.get('task_id')
print(f"Using task: {task_id[:8]} (status: {task.get('status')})")

# Send approval with auto_publish
print("\n[STEP 2] Sending approval with auto_publish=true...")
payload = {"approved": True, "auto_publish": True}
resp = requests.post(
    f"{BASE_URL}/api/tasks/{task_id}/approve",
    headers=HEADERS,
    json=payload
)

print(f"Response status: {resp.status_code}")
result = resp.json()
print(f"Response task status: {result.get('status')}")
print(f"Response has post_id: {bool(result.get('post_id'))}")

