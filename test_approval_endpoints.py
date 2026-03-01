#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

print("[TEST] Comprehensive Approval Endpoint Tests")
print("="*80)

# Get a task
resp = requests.get(
    f"{BASE_URL}/api/tasks?status=awaiting_approval&limit=1",
    headers=HEADERS
)

if resp.status_code != 200:
    print(f"Error fetching tasks: {resp.status_code}")
    exit(1)

tasks = resp.json().get("tasks", [])
if not tasks:
    print("No awaiting_approval tasks found")
    exit(1)

task = tasks[0]
task_id = task.get('id') or task.get('task_id')

print(f"\nUsing task: {task_id[:8]}")
print(f"Current status: {task.get('status')}")

# Test 1: Reject endpoint
print("\n[TEST 1] Test reject endpoint")
resp = requests.post(
    f"{BASE_URL}/api/tasks/{task_id}/reject",
    headers=HEADERS,
    json={"reason": "Test", "feedback": "Test feedback"}
)
print(f"Reject status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Response: {resp.text[:300]}")
else:
    result = resp.json()
    print(f"Result status: {result.get('status')}")
    if result.get('status') != 'rejected':
        print("[BUG] Expected status 'rejected'")

# Get fresh task
resp = requests.get(f"{BASE_URL}/api/tasks?status=rejected&limit=1", headers=HEADERS)
if resp.json().get("tasks"):
    task_id = resp.json()["tasks"][0].get('id') or resp.json()["tasks"][0].get('task_id')
    print(f"\nFound rejected task: {task_id[:8]}")

# Test 2: Check bulk-approve endpoint exists
print("\n[TEST 2] Check bulk-approve endpoint exists")
resp = requests.post(
    f"{BASE_URL}/api/tasks/bulk-approve",
    headers=HEADERS,
    json={"task_ids": ["fake-id"], "auto_publish": True}
)
print(f"Bulk-approve status: {resp.status_code}")
if resp.status_code == 404:
    print("[BUG] Bulk-approve endpoint not found (404)")
else:
    print(f"Response: {resp.text[:200]}")

# Test 3: Check posts endpoint
print("\n[TEST 3] Check posts endpoint filtering")
resp = requests.get(
    f"{BASE_URL}/api/posts?status=published&skip=0&limit=5",
    headers=HEADERS
)
print(f"Posts status: {resp.status_code}")
if resp.status_code == 200:
    posts = resp.json().get("data", [])
    print(f"Found {len(posts)} posts")
    if posts:
        post = posts[0]
        print(f"Sample post:")
        print(f"  status: {post.get('status')}")
        print(f"  featured_image_url: {bool(post.get('featured_image_url'))}")
        
        # Check for missing fields
        if not post.get('excerpt') or post.get('excerpt') == '':
            print("[WARNING] Post missing excerpt")
        if not post.get('seo_description') or post.get('seo_description') == '':
            print("[WARNING] Post missing seo_description")
else:
    print(f"[ERROR] {resp.status_code}")

print("\n" + "="*80)
