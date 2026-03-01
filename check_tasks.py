#!/usr/bin/env python3
import requests

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

statuses = ["awaiting_approval", "approved", "published", "pending"]

for status in statuses:
    resp = requests.get(f"{BASE_URL}/api/tasks?status={status}", headers=HEADERS)
    if resp.status_code == 200:
        count = resp.json().get('total', 0)
        print(f"{status}: {count}")
    else:
        print(f"{status}: Error {resp.status_code}")

# Get any task
print("\nAll tasks:")
resp = requests.get(f"{BASE_URL}/api/tasks?limit=1", headers=HEADERS)
if resp.status_code == 200:
    tasks = resp.json().get("tasks", [])
    if tasks:
        task = tasks[0]
        print(f"Found: {task.get('topic')} (status: {task.get('status')})")
