#!/usr/bin/env python3
"""Simple diagnosis of the database issue"""

import requests
import json

headers = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

task_id = "91175480-9244-4ca9-a727-129bdc4d7511"

print("\n[TEST] Fetching task from database...")
resp = requests.get(
    f"http://localhost:8000/api/tasks/{task_id}",
    headers=headers,
    timeout=10
)

if resp.status_code == 200:
    task = resp.json()
    print("\n[OK] Task fetched successfully")
    print(f"Task data:")
    print(f"  id: {task.get('id')}")
    print(f"  task_id: {task.get('task_id')}")
    print(f"  status: {task.get('status')}")
    print(f"  updated_at: {task.get('updated_at')}")
    print(f"\nFull task dump:")
    print(json.dumps(task, indent=2, default=str))
else:
    print(f"[ERROR] Failed to fetch: {resp.status_code}")
    print(resp.text)
