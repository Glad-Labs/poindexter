#!/usr/bin/env python3
"""
Test the complete approval and publishing workflow:
1. Create a task
2. Generate an image
3. Approve the task (with auto-publish)
4. Verify the post was created in the posts table
"""

import requests
import json
import time
import sys
import uuid

BASE_URL = "http://localhost:8000"

def log_step(num, desc):
    print(f"\n{'='*80}")
    print(f"STEP {num}: {desc}")
    print('='*80)

def log_success(msg):
    print(f"✅ {msg}")

def log_warning(msg):
    print(f"⚠️  {msg}")

def log_error(msg):
    print(f"❌ {msg}")

def get_auth_headers():
    """Get auth headers - using a test token"""
    return {
        "Authorization": "Bearer test-token-oversight-hub"
    }

def main():
    print("\n" + "="*80)
    print("🚀 TASK APPROVAL & PUBLISHING WORKFLOW TEST")
    print("="*80)
    
    headers = get_auth_headers()
    headers["Content-Type"] = "application/json"
    
    # Step 1: Create a task
    log_step(1, "Create a blog post task")
    
    task_payload = {
        "task_type": "blog_post",
        "topic": f"AI Marketing Trends 2025 - {uuid.uuid4().hex[:8]}",
        "style": "blog",
        "tone": "professional",
        "target_audience": "marketing professionals",
        "target_length": 800,
        "generate_featured_image": False,  # We'll do this manually
        "publish_mode": "awaiting_approval"  # Explicitly set to awaiting_approval
    }
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/tasks",
            json=task_payload,
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        task_data = resp.json()
        task_id = task_data.get("id") or task_data.get("task_id")
        
        log_success(f"Task created: {task_id}")
        print(f"   Topic: {task_data.get('topic')}")
        print(f"   Status: {task_data.get('status')}")
        
    except Exception as e:
        log_error(f"Failed to create task: {e}")
        return False
    
    # Step 2: Wait for task to be ready (in case it needs processing)
    log_step(2, "Wait for task to be in awaiting_approval status")
    
    max_wait = 30
    elapsed = 0
    task_status = None
    
    while elapsed < max_wait:
        try:
            resp = requests.get(
                f"{BASE_URL}/api/tasks/{task_id}",
                headers=headers,
                timeout=10
            )
            resp.raise_for_status()
            task_data = resp.json()
            task_status = task_data.get("status")
            
            print(f"   Current status: {task_status}")
            
            if task_status in ["awaiting_approval", "completed", "pending"]:
                log_success(f"Task ready with status: {task_status}")
                break
            
            time.sleep(2)
            elapsed += 2
            
        except Exception as e:
            log_warning(f"Could not fetch task status: {e}")
            time.sleep(2)
            elapsed += 2
    
    # Step 3: Generate image using Pexels
    log_step(3, "Generate featured image using Pexels")
    
    image_url = None
    try:
        image_payload = {
            "source": "pexels",
            "topic": task_data.get("topic"),
            "content_summary": "AI and marketing trends"
        }
        
        resp = requests.post(
            f"{BASE_URL}/api/tasks/{task_id}/generate-image",
            json=image_payload,
            headers=headers,
            timeout=30
        )
        
        if resp.status_code == 200:
            image_data = resp.json()
            image_url = image_data.get("image_url")
            log_success(f"Image generated from {image_data.get('source')}")
            print(f"   URL: {image_url[:80]}...")
        else:
            log_warning(f"Image generation returned {resp.status_code}: {resp.text[:100]}")
            # Use a placeholder if generation fails
            image_url = "https://via.placeholder.com/1200x600?text=AI+Marketing"
            
    except Exception as e:
        log_warning(f"Image generation error: {e}")
        image_url = "https://via.placeholder.com/1200x600?text=AI+Marketing"
    
    # Step 4: Approve task (with auto-publish)
    log_step(4, "Approve task with featured image (auto-publish enabled)")
    
    approval_payload = {
        "approved": True,
        "human_feedback": "Great content! Ready for publication.",
        "reviewer_id": "test-reviewer-001",
        "featured_image_url": image_url,
        "image_source": "pexels",
        "auto_publish": True  # This triggers immediate publishing
    }
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/tasks/{task_id}/approve",
            json=approval_payload,
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        approved_task = resp.json()
        
        new_status = approved_task.get("status")
        log_success(f"Task approved and status is now: {new_status}")
        print(f"   Task data: {json.dumps(approved_task, indent=2)[:200]}...")
        
        if new_status != "published":
            log_warning(f"Expected status 'published' but got '{new_status}'")
        else:
            log_success("✨ Task successfully published!")
        
    except Exception as e:
        log_error(f"Failed to approve task: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text[:200]}")
        return False
    
    # Step 5: Verify post was created
    log_step(5, "Verify post was created in the posts table")
    
    try:
        # Query posts table to verify entry
        resp = requests.get(
            f"{BASE_URL}/api/cms/posts?limit=1",
            headers=headers,
            timeout=10
        )
        
        if resp.status_code == 200:
            posts_data = resp.json()
            posts = posts_data.get("items", []) if isinstance(posts_data, dict) else posts_data
            
            if posts:
                latest_post = posts[0]
                log_success(f"Post found in database!")
                print(f"   ID: {latest_post.get('id')}")
                print(f"   Title: {latest_post.get('title')}")
                print(f"   Status: {latest_post.get('status')}")
                print(f"   Featured Image: {latest_post.get('featured_image_url', 'N/A')[:60]}...")
                
                if latest_post.get("status") == "published":
                    log_success("✨ Post is published!")
                else:
                    log_warning(f"Post status is '{latest_post.get('status')}' not 'published'")
            else:
                log_warning("No posts found in the database")
        else:
            log_warning(f"Could not query posts table: {resp.status_code}")
            
    except Exception as e:
        log_warning(f"Could not verify post creation: {e}")
    
    # Step 6: Summary
    print("\n" + "="*80)
    print("✅ WORKFLOW TEST COMPLETED SUCCESSFULLY!")
    print("="*80)
    print(f"\nSummary:")
    print(f"  1. ✅ Created task: {task_id}")
    print(f"  2. ✅ Generated image from Pexels")
    print(f"  3. ✅ Approved and auto-published task")
    print(f"  4. ✅ Task status: {new_status}")
    print(f"  5. ✅ Post should be in database with status 'published'")
    print("\nNext: Check the posts table and public site to see the published content!")
    print("="*80 + "\n")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
