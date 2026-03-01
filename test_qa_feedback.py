#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

print("[TEST] Check QA feedback in pending tasks")
print("="*80)

resp = requests.get(
    f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=3",
    headers=HEADERS,
    timeout=10
)

if resp.status_code == 200:
    tasks = resp.json().get("tasks", [])
    for i, task in enumerate(tasks):
        task_id = task.get('id') or task.get('task_id')
        print(f"\nTask {i+1}: {task_id[:8]}")
        print(f"  topic: {task.get('topic')}")
        print(f"  quality_score: {task.get('quality_score')}")
        print(f"  qa_feedback: {task.get('qa_feedback')}")
        
        # Check task result
        result = task.get('result')
        if isinstance(result, str) and result:
            try:
                result = json.loads(result)
                print(f"  result['qa_feedback']: {result.get('qa_feedback')}")
            except:
                pass
else:
    print(f"Error: {resp.status_code}")
