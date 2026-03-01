#!/usr/bin/env python3
"""
Comprehensive auto-publish workflow test
Tests the full approval > publishing > post creation pipeline
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test_auto_publish_workflow():
    print("\n" + "="*80)
    print("AUTO-PUBLISH WORKFLOW TEST")
    print("Testing complete approval > auto-publish > post creation flow")
    print("="*80)

    # Step 1: Create a blog post task
    print("\n[STEP 1] Creating blog post task...")
    task_payload = {
        "task_type": "blog_post",
        "topic": "Testing Auto-Publish Workflow Integration",
        "style": "technical",
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
        print(f"[FAIL] Failed to create task: {resp.status_code}: {resp.text}")
        return False

    task_data = resp.json()
    task_id = task_data.get("task_id") or task_data.get("id") or task_data.get("data", {}).get("id")
    print(f"[OK] Task created: {task_id}")

    # Step 2: Wait for task to reach awaiting_approval status
    print("\n[STEP 2] Waiting for content generation and approval readiness...")
    start = time.time()
    max_wait = 180  # 3 minutes

    task = None
    while time.time() - start < max_wait:
        resp = requests.get(
            f"{BASE_URL}/api/tasks/{task_id}",
            headers=HEADERS,
            timeout=10
        )

        if resp.status_code == 200:
            task = resp.json()
            status = task.get("status")

            if status == "awaiting_approval":
                elapsed = time.time() - start
                print(f"[OK] Task ready for approval after {elapsed:.0f}s")
                break

            if (time.time() - start) % 10 < 1:
                elapsed = time.time() - start
                print(f"    [{elapsed:.0f}s] Current status: {status}")

        time.sleep(2)
    else:
        print(f"[FAIL] Task did not reach awaiting_approval within {max_wait}s")
        return False

    # Step 3: Send approval WITH auto_publish=true
    print("\n[STEP 3] Sending approval request with auto_publish=true...")
    approval_payload = {
        "approved": True,
        "auto_publish": True,
        "feedback": "Auto-publish test",
        "human_feedback": "Integration test - please publish automatically"
    }

    print(f"    Payload: {json.dumps(approval_payload, indent=2)}")

    resp = requests.post(
        f"{BASE_URL}/api/tasks/{task_id}/approve",
        headers=HEADERS,
        json=approval_payload,
        timeout=10
    )

    print(f"    Response status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"[FAIL] Approval failed: {resp.status_code}: {resp.text[:300]}")
        return False

    approval_response = resp.json()
    print(f"[OK] Approval response received")
    print(f"    Status field: {approval_response.get('status') or approval_response.get('approval_status')}")
    print(f"    Has post_id: {bool(approval_response.get('post_id'))}")
    print(f"    Has post_slug: {bool(approval_response.get('post_slug'))}")

    # Step 4: Verify task status in database
    print("\n[STEP 4] Verifying task status in database...")
    time.sleep(2)  # Give database time to update

    resp = requests.get(
        f"{BASE_URL}/api/tasks/{task_id}",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code != 200:
        print(f"[FAIL] Could not fetch task: {resp.status_code}")
        return False

    task = resp.json()
    db_status = task.get("status")
    print(f"    Task status in DB: {db_status}")

    if db_status != "published":
        print(f"[FAIL] Task status is '{db_status}', expected 'published'")
        print(f"[ERROR] Auto-publish did NOT work - task is not published!")
        return False

    print(f"[OK] Task successfully published by auto-publish!")

    # Step 5: Check if post was created
    print("\n[STEP 5] Verifying post was created in posts table...")
    resp = requests.get(
        f"{BASE_URL}/api/posts?skip=0&limit=100",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code != 200:
        print(f"[FAIL] Could not fetch posts: {resp.status_code}")
        return False

    posts = resp.json().get("data", [])
    post_topic = task.get("topic", "")

    # Look for recent post matching the task topic
    matching_post = None
    for post in posts:
        post_title = post.get("title", "")
        if post_topic.lower()[:20] in post_title.lower() or post_topic.lower() in post_title.lower():
            matching_post = post
            break

    if not matching_post:
        print(f"[FAIL] Post not found for topic: {post_topic}")
        print(f"[ERROR] Post creation failed during auto-publish!")
        return False

    post_id = matching_post.get("id")
    post_slug = matching_post.get("slug")
    post_title = matching_post.get("title")

    print(f"[OK] Post created successfully!")
    print(f"    Post ID: {post_id}")
    print(f"    Post title: {post_title}")
    print(f"    Post slug: {post_slug}")

    # Final verification
    print("\n" + "="*80)
    print("[SUCCESS] AUTO-PUBLISH WORKFLOW COMPLETE!")
    print("="*80)
    print(f"  Task:  {task_id}")
    print(f"  Status: {db_status}")
    print(f"  Post:  {post_id}")
    print(f"  URL:   /posts/{post_slug}")
    print("="*80)

    return True


if __name__ == "__main__":
    try:
        success = test_auto_publish_workflow()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
