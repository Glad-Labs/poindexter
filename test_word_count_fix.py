#!/usr/bin/env python3
"""
Test script to verify word count validation and refinement fix
Tests that new blog posts meet word count targets
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test_word_count_enforcement():
    """Test that word count validation is enforced"""
    print("\n" + "="*80)
    print("WORD COUNT VALIDATION TEST")
    print("Testing Gemini refinement fix for short posts")
    print("="*80)

    # Get current published posts count
    print("\n[1] Getting baseline published posts count...")
    resp = requests.get(
        f"{BASE_URL}/api/posts?limit=100",
        headers=HEADERS,
        timeout=10
    )

    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch posts: {resp.status_code}")
        return False

    current_count = len(resp.json().get("data", []))
    print(f"    Current published posts: {current_count}")

    # Create a test task with explicit word count
    print("\n[2] Creating blog post task with target_length=1500...")
    task_payload = {
        "task_type": "blog_post",
        "topic": "Testing Word Count Enforcement in AI Content Generation",
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
        print(f"[ERROR] Failed to create task: {resp.status_code}: {resp.text}")
        return False

    task_data = resp.json()
    task_id = task_data.get("task_id") or task_data.get("id") or task_data.get("data", {}).get("id")

    if not task_id:
        print(f"[ERROR] No task_id in response: {task_data}")
        return False

    print(f"    Created task: {task_id}")

    # Wait for task completion
    print("\n[3] Waiting for content generation (120s timeout)...")
    start_wait = time.time()
    max_wait = 120

    while time.time() - start_wait < max_wait:
        resp = requests.get(
            f"{BASE_URL}/api/tasks/{task_id}",
            headers=HEADERS,
            timeout=10
        )

        if resp.status_code != 200:
            print(f"[ERROR] Failed to fetch task: {resp.status_code}")
            return False

        task = resp.json()
        status = task.get("status")
        stage = task.get("stage", "unknown")

        if status in ["awaiting_approval", "published", "approved", "completed"]:
            print(f"    Task {status} after {time.time() - start_wait:.0f}s")
            break

        elapsed = time.time() - start_wait
        if elapsed % 10 < 1:
            print(f"    [{elapsed:.0f}s] {status} - {stage}")

        time.sleep(1)
    else:
        print(f"[ERROR] Task did not complete within {max_wait}s")
        return False

    # Analyze generated content
    print("\n[4] Analyzing generated content...")
    result = task.get("result")

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except:
            result = {}

    if not result or not isinstance(result, dict):
        print(f"[ERROR] Invalid result format")
        return False

    content = result.get("content", "")
    word_count = len(content.split())
    quality_score = result.get("quality_score", 0)
    seo_keywords = result.get("seo_keywords")
    featured_image = result.get("featured_image_url")

    target_length = task.get("target_length", 1500)
    min_acceptable = int(target_length * 0.7)
    max_acceptable = int(target_length * 1.3)

    print(f"    Content length: {word_count} words")
    print(f"    Target: {target_length} words (±30% = {min_acceptable}-{max_acceptable})")
    print(f"    Quality score: {quality_score}")
    print(f"    Featured image: {'✓' if featured_image else '✗'}")
    print(f"    SEO keywords: {'✓' if seo_keywords else '✗'}")

    # Perform validation checks
    print("\n[5] Validation Results:")
    checks = {
        "word_count_acceptable": min_acceptable <= word_count <= max_acceptable,
        "over_minimum": word_count >= 1000,
        "quality_threshold": float(quality_score) >= 60,
        "has_featured_image": bool(featured_image),
        "has_seo_keywords": bool(seo_keywords)
    }

    for check, passed in checks.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"    {status} {check}")

    all_passed = all(checks.values())

    if all_passed:
        print("\n" + "="*80)
        print("[SUCCESS] Word count validation is working correctly!")
        print("="*80)
        return True
    else:
        print("\n" + "="*80)
        print("[FAILURE] Word count validation issues detected:")
        for check, passed in checks.items():
            if not passed:
                print(f"  - {check}")
        print("="*80)
        return False

if __name__ == "__main__":
    try:
        success = test_word_count_enforcement()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
