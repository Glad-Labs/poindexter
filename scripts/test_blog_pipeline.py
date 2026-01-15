#!/usr/bin/env python3
"""Test the blog post pipeline end-to-end"""

import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

def test_blog_pipeline():
    print("\n" + "="*80)
    print("🚀 BLOG POST PIPELINE TEST")
    print("="*80 + "\n")
    
    # Step 1: Create task
    print("📋 Step 1: Creating blog post task...")
    payload = {
        "task_type": "blog_post",
        "topic": "Test Blog: Quantum Computing",
        "style": "technical",
        "tone": "informative",
        "target_length": 1200,
        "generate_featured_image": True,
        "publish_mode": "draft"
    }
    
    resp = requests.post(f"{BASE_URL}/api/content/tasks", json=payload)
    resp.raise_for_status()
    task_data = resp.json()
    task_id = task_data["task_id"]
    
    print(f"✅ Task created: {task_id}")
    print(f"   Status: {task_data['status']}")
    print(f"   Created: {task_data['created_at']}\n")
    
    # Step 2: Monitor progress
    print("⏳ Step 2: Waiting for content generation...\n")
    max_wait = 60
    elapsed = 0
    last_status = task_data["status"]
    current_task = task_data
    
    while elapsed < max_wait:
        time.sleep(2)
        elapsed += 2
        
        resp = requests.get(f"{BASE_URL}/api/content/tasks/{task_id}")
        resp.raise_for_status()
        current_task = resp.json()
        current_status = current_task["status"]
        
        if current_status != last_status:
            print(f"[{elapsed}s] Status: {last_status} → {current_status}")
            last_status = current_status
        
        if current_status == "awaiting_approval":
            print(f"\n✅ Task reached 'awaiting_approval' status\n")
            break
        
        if current_status == "failed":
            print(f"\n❌ Task failed!")
            print(f"Error: {current_task.get('error_message', 'Unknown')}\n")
            return False
    
    # Step 3: Check content
    print("🔍 Step 3: Checking generated content...\n")
    
    content_len = len(current_task.get("content") or "")
    excerpt = current_task.get("excerpt") or "[NULL]"
    if len(excerpt) > 50:
        excerpt = excerpt[:50] + "..."
    
    print(f"Content Status:")
    print(f"   - Length: {content_len} chars")
    print(f"   - Excerpt: {excerpt}")
    print(f"   - Featured image: {'✅' if current_task.get('featured_image_url') else '❌ NULL'}")
    print(f"   - SEO title: {'✅' if current_task.get('seo_title') else '❌ NULL'}")
    print(f"   - Quality score: {current_task.get('quality_score')}\n")
    
    if content_len == 0:
        print("⚠️  WARNING: No content generated!")
        print(f"\nFull task data:\n{json.dumps(current_task, indent=2, default=str)}\n")
        return False
    
    # Step 4: Approve
    print("📝 Step 4: Approving task...\n")
    
    approval_payload = {
        "approved": True,
        "human_feedback": "Approved by test script",
        "reviewer_id": "test-script"
    }
    
    resp = requests.post(f"{BASE_URL}/api/content/tasks/{task_id}/approve", json=approval_payload)
    resp.raise_for_status()
    approval_data = resp.json()
    
    print(f"Approval Result:")
    print(f"   - Status: {approval_data.get('approval_status')}")
    print(f"   - Published URL: {approval_data.get('published_url')}")
    print(f"   - Post ID: {approval_data.get('strapi_post_id')}\n")
    
    # Step 5: Check posts table
    print("📚 Step 5: Checking posts table...\n")
    
    resp = requests.get(f"{BASE_URL}/api/posts")
    resp.raise_for_status()
    posts_data = resp.json()
    
    post_id = approval_data.get("strapi_post_id")
    found_post = None
    for post in posts_data.get("data", []):
        if post.get("id") == post_id:
            found_post = post
            break
    
    if found_post:
        post_content_len = len(found_post.get("content") or "")
        print(f"✅ Blog post found in database!")
        print(f"   - ID: {found_post['id']}")
        print(f"   - Title: {found_post.get('title')}")
        print(f"   - Content length: {post_content_len} chars")
        print(f"   - Status: {found_post.get('status')}\n")
        return post_content_len > 0
    else:
        print(f"❌ Blog post NOT found in posts table!")
        print(f"   Looking for ID: {post_id}")
        print(f"   Total posts: {len(posts_data.get('data', []))}\n")
        return False

if __name__ == "__main__":
    try:
        success = test_blog_pipeline()
        print("="*80)
        if success:
            print("✅ PIPELINE TEST PASSED")
        else:
            print("❌ PIPELINE TEST FAILED")
        print("="*80 + "\n")
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)
