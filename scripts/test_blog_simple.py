#!/usr/bin/env python3
"""Simple test of blog pipeline"""

import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

print("\n=== BLOG POST PIPELINE TEST ===\n")

# Step 1: Create task
print("[1] Creating blog post task...")
payload = {
    "task_type": "blog_post",
    "topic": "Simple Test Topic",
    "style": "technical",
    "tone": "professional",
    "target_length": 1200,
}

try:
    resp = requests.post(f"{BASE_URL}/api/content/tasks", json=payload)
    print(f"    Response status: {resp.status_code}")
    if resp.status_code >= 400:
        print(f"    Error: {resp.text[:200]}")
        sys.exit(1)
    task_data = resp.json()
    task_id = task_data["task_id"]
    print(f"    Task ID: {task_id}")
except Exception as e:
    print(f"    ERROR: {e}")
    sys.exit(1)

# Step 2: Wait and check
print("\n[2] Waiting for content generation...")
task = None
for i in range(30):
    time.sleep(2)
    try:
        resp = requests.get(f"{BASE_URL}/api/content/tasks/{task_id}")
        task = resp.json()
        status = task.get("status")
        content_len = len(task.get("content") or "")
        
        print(f"    [{i*2}s] Status: {status}, Content: {content_len} chars")
        
        if status == "awaiting_approval":
            print(f"\n    READY FOR APPROVAL")
            break
        if status == "failed":
            print(f"\n    FAILED")
            sys.exit(1)
    except Exception as e:
        print(f"    ERROR: {e}")
        sys.exit(1)

# Step 3: Check content
print("\n[3] Checking generated content...")
print(f"    Content length: {len(task.get('content') or '')} chars")
print(f"    Excerpt: {(task.get('excerpt') or '[NULL]')[:50]}...")
print(f"    Featured image: {'YES' if task.get('featured_image_url') else 'NO'}")
print(f"    SEO title: {'YES' if task.get('seo_title') else 'NO'}")
print(f"    Quality score: {task.get('quality_score')}")

if len(task.get("content") or "") == 0:
    print("\n[ERROR] No content generated")
    print(json.dumps(task, indent=2, default=str)[:500])
    sys.exit(1)

# Step 4: Approve
print("\n[4] Approving task...")
resp = requests.post(
    f"{BASE_URL}/api/content/tasks/{task_id}/approve",
    json={
        "approved": True,
        "human_feedback": "OK",
        "reviewer_id": "test"
    }
)
print(f"    Status: {resp.status_code}")
if resp.status_code >= 400:
    print(f"    Error: {resp.text}")
    sys.exit(1)

approval = resp.json()
post_id = approval.get("strapi_post_id")
print(f"    Post ID: {post_id}")

# Step 5: Check posts table
print("\n[5] Checking posts table...")
resp = requests.get(f"{BASE_URL}/api/posts")
posts = resp.json()
found = False
for post in posts.get("data", []):
    if post.get("id") == post_id:
        found = True
        print(f"    Post found in DB")
        print(f"    Content length: {len(post.get('content') or '')} chars")
        break

if found:
    print("\n=== SUCCESS ===\n")
else:
    print("\n=== FAILED: Post not in database ===\n")
    sys.exit(1)
