#!/usr/bin/env python3
"""
Explicit auto-publish test - finds an awaiting_approval task and tests auto_publish
"""
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test():
    print("\n[1] Fetching approved tasks (to test publish endpoint)...")
    resp = requests.get(
        f"{BASE_URL}/api/tasks?status=approved&limit=5",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code == 200:
        data = resp.json()
        approved_tasks = data.get("tasks", [])
        print(f"    Found {len(approved_tasks)} approved tasks")
        
        if approved_tasks:
            task = approved_tasks[0]
            task_id = task.get("id") or task.get("task_id")
            print(f"\n[2] Testing auto-publish on approved task: {task_id}")
            
            # Try to approve it again with auto_publish
            resp = requests.post(
                f"{BASE_URL}/api/tasks/{task_id}/approve",
                headers=HEADERS,
                json={"approved": True, "auto_publish": True},
                timeout=10
            )
            
            print(f"    Response status code: {resp.status_code}")
            if resp.status_code == 200:
                response = resp.json()
                print(f"    Response status field: {response.get('status') or response.get('approval_status')}")
                print(f"    Has post_id: {bool(response.get('post_id'))}")
            else:
                print(f"    Error: {resp.text[:300]}")

test()
