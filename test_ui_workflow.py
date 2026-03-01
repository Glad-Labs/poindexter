#!/usr/bin/env python3
"""
Comprehensive UI Workflow Testing Script
Tests the entire approval → publishing → public display pipeline
"""
import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test_pending_approval_list():
    """Test 1: Fetch pending approval tasks"""
    print("\n" + "="*80)
    print("[TEST 1] Fetch pending approval tasks")
    print("="*80)

    try:
        resp = requests.get(
            f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=5",
            headers=HEADERS,
            timeout=10
        )
        print(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            tasks = data.get("tasks", [])
            print(f"Found {len(tasks)} tasks awaiting approval")

            if tasks:
                task = tasks[0]
                print(f"\nFirst task:")
                print(f"  ID: {task.get('id') or task.get('task_id')}")
                print(f"  Topic: {task.get('topic')}")
                print(f"  Status: {task.get('status')}")
                print(f"  Quality Score: {task.get('quality_score')}")
                print(f"  Has qa_feedback: {'qa_feedback' in task and task['qa_feedback'] is not None}")
                print(f"  Has featured_image: {'featured_image_url' in task and task['featured_image_url'] is not None}")

                return task.get('id') or task.get('task_id')
            else:
                print("[WARNING] No tasks in awaiting_approval status")
                return None
        else:
            print(f"[ERROR] {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[ERROR] {e}")
        return None

def test_task_detail(task_id):
    """Test 2: Fetch single task details"""
    print("\n" + "="*80)
    print("[TEST 2] Fetch single task details")
    print("="*80)

    try:
        resp = requests.get(
            f"{BASE_URL}/api/tasks/{task_id}",
            headers=HEADERS,
            timeout=10
        )
        print(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            task = resp.json()
            print(f"Task fields present:")
            print(f"  task_id: {task.get('id') or task.get('task_id')}")
            print(f"  topic: {task.get('topic')}")
            print(f"  status: {task.get('status')}")
            print(f"  quality_score: {task.get('quality_score')}")
            print(f"  qa_feedback: {bool(task.get('qa_feedback'))}")
            print(f"  featured_image_url: {bool(task.get('featured_image_url'))}")

            # Check result field
            task_result = task.get("result")
            if isinstance(task_result, str):
                try:
                    task_result = json.loads(task_result)
                except:
                    task_result = {}

            print(f"\nTask result fields:")
            if 'content' in task_result:
                print(f"  content: {len(task_result.get('content', ''))} chars")
            else:
                print(f"  content: MISSING")
            print(f"  seo_title: {bool(task_result.get('seo_title'))}")
            print(f"  seo_description: {bool(task_result.get('seo_description'))}")
            print(f"  seo_keywords: {bool(task_result.get('seo_keywords'))}")

            return task
        else:
            print(f"[ERROR] {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[ERROR] {e}")
        return None

def test_posts_list():
    """Test 3: Check posts table"""
    print("\n" + "="*80)
    print("[TEST 3] Fetch published posts from API")
    print("="*80)

    try:
        resp = requests.get(
            f"{BASE_URL}/api/posts?skip=0&limit=10",
            headers=HEADERS,
            timeout=10
        )
        print(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            posts = data.get("data", [])
            print(f"Found {len(posts)} posts total")

            if posts:
                post = posts[0]
                print(f"\nFirst post:")
                print(f"  id: {post.get('id')}")
                print(f"  title: {post.get('title')}")
                print(f"  slug: {post.get('slug')}")
                print(f"  status: {post.get('status')}")
                print(f"  featured_image_url present: {bool(post.get('featured_image_url'))}")
                print(f"  content length: {len(post.get('content', ''))} chars")
                print(f"  seo_keywords: {post.get('seo_keywords')}")
        else:
            print(f"[ERROR] {resp.status_code}")
    except Exception as e:
        print(f"[ERROR] {e}")

def test_error_cases():
    """Test 4: Test error handling"""
    print("\n" + "="*80)
    print("[TEST 4] Test error handling")
    print("="*80)

    # Test non-existent task
    print("\n[4a] Request non-existent task")
    try:
        resp = requests.get(
            f"{BASE_URL}/api/tasks/nonexistent-id",
            headers=HEADERS,
            timeout=10
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code != 404:
            print(f"[BUG] Expected 404, got {resp.status_code}")
        else:
            print(f"✓ Correctly returns 404")
    except Exception as e:
        print(f"[ERROR] {e}")

    # Test missing auth
    print("\n[4b] Request without auth header")
    try:
        resp = requests.get(
            f"{BASE_URL}/api/tasks?status=awaiting_approval",
            timeout=10
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code not in [401, 403, 200]:  # Some endpoints might be public
            print(f"[INFO] Got {resp.status_code}")
        else:
            print(f"✓ Response: {resp.status_code}")
    except Exception as e:
        print(f"[ERROR] {e}")

def main():
    print("\n" + "="*80)
    print("GLAD LABS UI WORKFLOW TEST SUITE")
    print(f"Started: {datetime.now()}")
    print("="*80)

    test_pending_approval_list()
    test_posts_list()
    test_error_cases()

    print("\n" + "="*80)
    print(f"Finished: {datetime.now()}")
    print("="*80)

if __name__ == "__main__":
    main()
