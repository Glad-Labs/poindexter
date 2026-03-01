#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123", "Content-Type": "application/json"}

resp = requests.get(f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1", headers=HEADERS)
tasks = resp.json().get("tasks", [])
task_id = tasks[0].get('id') or tasks[0].get('task_id')

# Send exact JSON
payload = {
    "approved": True,
    "auto_publish": True,
    "human_feedback": None
}

print(f"Sending JSON: {json.dumps(payload)}")
print(f"Content-Type: {HEADERS.get('Content-Type')}")

resp = requests.post(
    f"{BASE_URL}/api/tasks/{task_id}/approve",
    headers=HEADERS,
    json=payload
)

print(f"\nResponse status: {resp.status_code}")
result = resp.json()
print(f"Response status field: {result.get('status')}")
print(f"Full response keys: {list(result.keys())}")

