#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

resp = requests.get(f"{BASE_URL}/api/tasks?status=approved&limit=1", headers=HEADERS)
task_id = resp.json()["tasks"][0].get('id') or resp.json()["tasks"][0].get('task_id')

print(f"Testing approval of approved task: {task_id[:8]}")

resp = requests.post(
    f"{BASE_URL}/api/tasks/{task_id}/approve",
    headers=HEADERS,
    json={"approved": True, "auto_publish": True}
)

print(f"Status: {resp.status_code}")
print(f"Response: {json.dumps(resp.json(), indent=2)}")
