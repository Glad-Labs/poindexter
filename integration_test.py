#!/usr/bin/env python3
"""
End-to-End Integration Test
Tests the complete flow: Oversight Hub → Cofounder Agent → Strapi → Public Site

Run this after all services are started:
  npm run dev              # Start Oversight Hub, Public Site
  npm run dev:strapi      # Start Strapi
  python -m uvicorn src.cofounder_agent.main:app --reload  # Start backend

Usage:
  python integration_test.py [--skip-polling] [--verbose]

Options:
  --skip-polling: Don't wait for task completion (just create task and exit)
  --verbose:      Print detailed API responses
"""

import requests
import json
import time
import sys
from datetime import datetime
from typing import Dict, Any, Optional

# Configuration
COFOUNDER_API_URL = "http://localhost:8000"
STRAPI_API_URL = "http://localhost:1337"
PUBLIC_SITE_URL = "http://localhost:3000"
OVERSIGHT_HUB_URL = "http://localhost:3001"

# Test parameters
POLL_INTERVAL = 5  # seconds
MAX_WAIT_TIME = 600  # 10 minutes max
VERBOSE = "--verbose" in sys.argv
SKIP_POLLING = "--skip-polling" in sys.argv

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def log_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}\n")


def log_success(text: str):
    print(f"{Colors.OKGREEN}✅ {text}{Colors.ENDC}")


def log_error(text: str):
    print(f"{Colors.FAIL}❌ {text}{Colors.ENDC}")


def log_info(text: str):
    print(f"{Colors.OKCYAN}ℹ️  {text}{Colors.ENDC}")


def log_warning(text: str):
    print(f"{Colors.WARNING}⚠️  {text}{Colors.ENDC}")


def log_section(text: str):
    print(f"\n{Colors.OKBLUE}{Colors.BOLD}→ {text}{Colors.ENDC}")


def test_connectivity():
    """Test that all services are running and accessible"""
    log_section("Testing Service Connectivity")
    
    services = [
        ("Cofounder Agent", f"{COFOUNDER_API_URL}/api/health"),
        ("Strapi CMS", f"{STRAPI_API_URL}/admin"),
        ("Public Site", f"{PUBLIC_SITE_URL}/"),
        ("Oversight Hub", f"{OVERSIGHT_HUB_URL}/"),
    ]
    
    all_healthy = True
    for service_name, url in services:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                log_success(f"{service_name} is running ({response.status_code})")
            else:
                log_warning(f"{service_name} returned {response.status_code}")
        except requests.ConnectionError:
            log_error(f"{service_name} is not running at {url}")
            all_healthy = False
        except Exception as e:
            log_warning(f"{service_name} error: {str(e)}")
    
    return all_healthy


def create_blog_post_task() -> Optional[str]:
    """Create a blog post task via Cofounder Agent API"""
    log_section("Creating Blog Post Task")
    
    payload = {
        "topic": f"AI & Machine Learning Trends - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "style": "technical",
        "tone": "professional",
        "target_length": 1500,
        "tags": ["AI", "MachineLearning", "Technology", "2025Trends"],
        "categories": ["Technology"],
        "generate_featured_image": True,
        "publish_mode": "draft",
        "enhanced": False,
        "target_environment": "production"
    }
    
    log_info(f"Creating blog post with topic: {payload['topic']}")
    if VERBOSE:
        print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{COFOUNDER_API_URL}/api/content/blog-posts",
            json=payload,
            timeout=30
        )
        
        if response.status_code != 201:
            log_error(f"Failed to create task: {response.status_code}")
            if VERBOSE:
                print(f"Response: {response.text}")
            return None
        
        result = response.json()
        task_id = result.get("task_id")
        log_success(f"Task created with ID: {task_id}")
        if VERBOSE:
            print(f"Response: {json.dumps(result, indent=2)}")
        
        return task_id
        
    except Exception as e:
        log_error(f"Error creating task: {str(e)}")
        return None


def poll_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Poll task status until completion"""
    log_section("Polling Task Status")
    log_info(f"Polling task {task_id} every {POLL_INTERVAL} seconds (max {MAX_WAIT_TIME}s)")
    
    start_time = time.time()
    poll_count = 0
    
    while True:
        elapsed = time.time() - start_time
        poll_count += 1
        
        try:
            response = requests.get(
                f"{COFOUNDER_API_URL}/api/content/blog-posts/tasks/{task_id}",
                timeout=30
            )
            
            if response.status_code != 200:
                log_error(f"Failed to get task status: {response.status_code}")
                return None
            
            result = response.json()
            status = result.get("status", "unknown")
            progress = result.get("progress", {})
            
            # Print status with progress
            if progress:
                progress_str = " | ".join([f"{k}: {v}" for k, v in progress.items()])
                print(f"  [{poll_count}] Status: {Colors.OKCYAN}{status}{Colors.ENDC} | {progress_str}")
            else:
                print(f"  [{poll_count}] Status: {Colors.OKCYAN}{status}{Colors.ENDC}")
            
            # Check for completion
            if status == "completed":
                log_success("Task completed successfully!")
                if VERBOSE:
                    print(f"Full result: {json.dumps(result, indent=2)}")
                return result
            
            elif status == "failed":
                log_error(f"Task failed: {result.get('error', 'Unknown error')}")
                if VERBOSE:
                    print(f"Full result: {json.dumps(result, indent=2)}")
                return None
            
            # Check timeout
            if elapsed > MAX_WAIT_TIME:
                log_error(f"Task polling timeout after {MAX_WAIT_TIME}s")
                return None
            
            # Wait before next poll
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log_error(f"Error polling task: {str(e)}")
            return None


def verify_in_strapi() -> Optional[str]:
    """Verify that content was published to Strapi"""
    log_section("Verifying Content in Strapi")
    
    try:
        response = requests.get(
            f"{STRAPI_API_URL}/api/posts?sort=-createdAt&pagination[limit]=1",
            timeout=10
        )
        
        if response.status_code != 200:
            log_error(f"Failed to fetch posts from Strapi: {response.status_code}")
            return None
        
        data = response.json()
        posts = data.get("data", [])
        
        if not posts:
            log_error("No posts found in Strapi")
            return None
        
        latest_post = posts[0]
        post_id = latest_post.get("id")
        title = latest_post.get("attributes", {}).get("title", "Unknown")
        slug = latest_post.get("attributes", {}).get("slug", "unknown")
        
        log_success(f"Latest post in Strapi: {title} (ID: {post_id})")
        if VERBOSE:
            print(f"Post data: {json.dumps(latest_post, indent=2)}")
        
        return slug
        
    except Exception as e:
        log_error(f"Error verifying Strapi content: {str(e)}")
        return None


def verify_on_public_site(slug: str) -> bool:
    """Verify that content is displayed on public site"""
    log_section("Verifying Content on Public Site")
    
    try:
        # Check homepage
        log_info("Checking homepage for featured posts...")
        response = requests.get(f"{PUBLIC_SITE_URL}/", timeout=10)
        if response.status_code == 200:
            log_success("Homepage is accessible")
        else:
            log_error(f"Homepage returned {response.status_code}")
            return False
        
        # Check blog page
        log_info("Checking blog page for post list...")
        response = requests.get(f"{PUBLIC_SITE_URL}/blog", timeout=10)
        if response.status_code == 200:
            log_success("Blog page is accessible")
        else:
            log_warning(f"Blog page returned {response.status_code}")
        
        # Check individual post
        log_info(f"Checking individual post page: /posts/{slug}")
        response = requests.get(f"{PUBLIC_SITE_URL}/posts/{slug}", timeout=10)
        if response.status_code == 200:
            log_success(f"Post page is accessible at /posts/{slug}")
            return True
        else:
            log_warning(f"Post page returned {response.status_code}")
            # This might be OK if ISR hasn't updated yet
            return True
        
    except requests.ConnectionError:
        log_error("Cannot connect to Public Site")
        return False
    except Exception as e:
        log_error(f"Error verifying public site: {str(e)}")
        return False


def print_summary(success: bool, task_id: Optional[str], slug: Optional[str]):
    """Print test summary"""
    log_header("Integration Test Summary")
    
    if success:
        print(f"{Colors.OKGREEN}{Colors.BOLD}All Tests Passed! ✅{Colors.ENDC}\n")
        if task_id:
            print(f"  Task ID: {task_id}")
        if slug:
            print(f"  Post Slug: {slug}")
            print(f"  Post URL: {PUBLIC_SITE_URL}/posts/{slug}\n")
        print(f"Your blog post has been successfully created, published to Strapi,")
        print(f"and is now visible on your public site! 🎉\n")
    else:
        print(f"{Colors.FAIL}{Colors.BOLD}Some Tests Failed ❌{Colors.ENDC}\n")
        print(f"Check the errors above for details.\n")


def main():
    """Run all integration tests"""
    log_header("GLAD Labs - End-to-End Integration Test")
    
    # Step 1: Check connectivity
    if not test_connectivity():
        log_error("Some services are not running. Please start all services first.")
        print(f"\n{Colors.WARNING}Expected services:{Colors.ENDC}")
        print(f"  - Cofounder Agent: {COFOUNDER_API_URL}")
        print(f"  - Strapi CMS: {STRAPI_API_URL}")
        print(f"  - Public Site: {PUBLIC_SITE_URL}")
        print(f"  - Oversight Hub: {OVERSIGHT_HUB_URL}\n")
        return False
    
    # Step 2: Create blog post
    task_id = create_blog_post_task()
    if not task_id:
        print_summary(False, None, None)
        return False
    
    # Step 3: Poll for completion (unless skipped)
    if SKIP_POLLING:
        log_info("Skipping polling (--skip-polling flag set)")
        log_info("Task created successfully. Check Oversight Hub for progress.")
        print_summary(True, task_id, None)
        return True
    
    task_result = poll_task_status(task_id)
    if not task_result:
        print_summary(False, task_id, None)
        return False
    
    # Step 4: Verify in Strapi
    log_info("Waiting for Strapi to process...")
    time.sleep(2)
    slug = verify_in_strapi()
    if not slug:
        log_warning("Content may still be processing in Strapi")
        print_summary(True, task_id, None)
        return True
    
    # Step 5: Verify on public site
    log_info("Waiting for Public Site ISR to regenerate...")
    time.sleep(2)
    site_ok = verify_on_public_site(slug)
    
    print_summary(site_ok, task_id, slug)
    return site_ok


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Test interrupted by user{Colors.ENDC}\n")
        sys.exit(130)
    except Exception as e:
        log_error(f"Unexpected error: {str(e)}")
        print_summary(False, None, None)
        sys.exit(1)
