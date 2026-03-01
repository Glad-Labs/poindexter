#!/usr/bin/env python3
"""
Debug test to check if auto_publish parameter is being recognized
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test_auto_publish():
    print("\n" + "="*80)
    print("AUTO-PUBLISH PARAMETER DEBUG TEST")
    print("="*80)

    # First, get a task that's awaiting approval
    print("\n[1] Fetching task awaiting approval...")
    resp = requests.get(
        f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch tasks: {resp.status_code}")
        return False

    data = resp.json()
    tasks = data.get("tasks", [])

    if not tasks:
        print("[INFO] No tasks awaiting approval. Creating one...")

        # Create a task
        task_payload = {
            "task_type": "blog_post",
            "topic": "Testing Auto-Publish Parameter",
            "style": "narrative",
            "tone": "professional",
            "target_length": 1500,
            "generate_featured_image": True,
            "quality_preference": "balanced"
        }

        resp = requests.post(
            f"{BASE_URL}/api/tasks",
            headers=HEADERS,
            json=task_payload,
            timeout=10
        )

        if resp.status_code not in [200, 202]:
            print(f"[ERROR] Failed to create task: {resp.status_code}")
            return False

        task_data = resp.json()
        task_id = task_data.get("task_id") or task_data.get("id") or task_data.get("data", {}).get("id")
        print(f"[OK] Created task: {task_id}")

        # Wait for completion
        print("[WAIT] Waiting for task to reach awaiting_approval status...")
        start = time.time()
        while time.time() - start < 120:
            resp = requests.get(
                f"{BASE_URL}/api/tasks/{task_id}",
                headers=HEADERS,
                timeout=10
            )
            if resp.status_code == 200:
                task = resp.json()
                if task.get("status") == "awaiting_approval":
                    print(f"[OK] Task reached awaiting_approval status")
                    break
            time.sleep(5)
    else:
        task = tasks[0]
        task_id = task.get("id") or task.get("task_id")
        print(f"[OK] Found task: {task_id}")

    # Test 1: Send approval WITHOUT auto_publish
    print("\n[2] TEST 1: Sending approval without auto_publish...")
    approval_payload = {
        "approved": True,
        "feedback": "Test approval"
    }

    print(f"    Payload: {json.dumps(approval_payload)}")
    resp = requests.post(
        f"{BASE_URL}/api/tasks/{task_id}/approve",
        headers=HEADERS,
        json=approval_payload,
        timeout=10
    )

    if resp.status_code == 200:
        response = resp.json()
        status = response.get("status") or response.get("approval_status")
        print(f"    [OK] Response status: {status}")
        print(f"    Has post_id: {bool(response.get('post_id'))}")
    else:
        print(f"    [ERROR] {resp.status_code}: {resp.text[:200]}")

    # Get fresh task for next test
    resp = requests.get(
        f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code != 200:
        print("[ERROR] No more tasks for second test")
        return False

    tasks = resp.json().get("tasks", [])
    if not tasks:
        print("[SKIP] No awaiting_approval tasks available")
        return True

    task = tasks[0]
    task_id = task.get("id") or task.get("task_id")

    # Test 2: Send approval WITH auto_publish
    print("\n[3] TEST 2: Sending approval WITH auto_publish=true...")
    approval_payload = {
        "approved": True,
        "auto_publish": True,
        "feedback": "Test approval with auto-publish"
    }

    print(f"    Payload: {json.dumps(approval_payload)}")
    resp = requests.post(
        f"{BASE_URL}/api/tasks/{task_id}/approve",
        headers=HEADERS,
        json=approval_payload,
        timeout=10
    )

    if resp.status_code == 200:
        response = resp.json()
        status = response.get("status") or response.get("approval_status")
        post_id = response.get("post_id")
        post_slug = response.get("post_slug")

        print(f"    [OK] Response status: {status}")
        print(f"    Response has post_id: {bool(post_id)}")
        print(f"    Response has post_slug: {bool(post_slug)}")

        if post_id:
            print(f"    Post ID: {post_id}")
        if post_slug:
            print(f"    Post slug: {post_slug}")

        # Verify in database
        resp = requests.get(
            f"{BASE_URL}/api/tasks/{task_id}",
            headers=HEADERS,
            timeout=10
        )

        if resp.status_code == 200:
            task = resp.json()
            db_status = task.get("status")
            print(f"\n    [DB] Task status in database: {db_status}")

            if db_status == "published":
                print(f"    [OK] Task published correctly!")
                return True
            else:
                print(f"    [ERROR] Task status is '{db_status}', not 'published'")
                print(f"    [ERROR] Auto-publish did NOT work")
                return False
    else:
        print(f"    [ERROR] {resp.status_code}: {resp.text[:200]}")
        return False

if __name__ == "__main__":
    try:
        success = test_auto_publish()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
