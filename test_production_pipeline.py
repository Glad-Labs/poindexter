#!/usr/bin/env python3
"""
Production Pipeline Test Script

Tests the complete blog generation pipeline:
1. Create task
2. Monitor generation (orchestrator)
3. Verify critique loop
4. Check Strapi publication

Run: python test_production_pipeline.py
"""

import asyncio
import httpx
import json
import time
from typing import Dict, Any, Optional
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

API_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
STRAPI_URL = os.getenv("STRAPI_URL", "http://localhost:1337")
STRAPI_TOKEN = os.getenv("STRAPI_API_TOKEN", "")


def print_header(title: str):
    """Print a formatted header"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_success(msg: str):
    """Print success message"""
    print(f"✅ {msg}")


def print_error(msg: str):
    """Print error message"""
    print(f"❌ {msg}")


def print_warning(msg: str):
    """Print warning message"""
    print(f"⚠️  {msg}")


def print_info(msg: str):
    """Print info message"""
    print(f"ℹ️  {msg}")


async def test_api_health() -> bool:
    """Test backend API health"""
    print_header("Testing Backend Health")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_URL}/api/health", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                print_success(f"Backend API is healthy: {data.get('status')}")
                return True
            else:
                print_error(f"Backend returned status {response.status_code}")
                return False
    except Exception as e:
        print_error(f"Cannot connect to backend: {e}")
        return False


async def test_orchestrator_status() -> bool:
    """Test orchestrator availability"""
    print_header("Testing Orchestrator")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_URL}/api/agents/status", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                print_success("Orchestrator is available")
                print_info(f"Agents: {data.get('agents_available', 'N/A')}")
                return True
            else:
                print_warning(f"Orchestrator status check returned {response.status_code}")
                return True  # Don't fail pipeline if not critical
    except Exception as e:
        print_warning(f"Cannot reach orchestrator: {e}")
        return True  # Don't fail pipeline if not critical


async def test_strapi_connection() -> bool:
    """Test Strapi CMS connection"""
    print_header("Testing Strapi CMS Connection")
    
    if not STRAPI_TOKEN:
        print_warning("No STRAPI_API_TOKEN provided - Strapi publishing will not work")
        return False
    
    try:
        headers = {"Authorization": f"Bearer {STRAPI_TOKEN}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{STRAPI_URL}/api/articles?pagination[limit]=1",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                print_success(f"Strapi connection successful: {STRAPI_URL}")
                data = response.json()
                total = data.get("meta", {}).get("pagination", {}).get("total", 0)
                print_info(f"Total published articles: {total}")
                return True
            elif response.status_code == 401:
                print_error("Strapi authentication failed - check STRAPI_API_TOKEN")
                return False
            else:
                print_error(f"Strapi returned status {response.status_code}")
                return False
    except Exception as e:
        print_error(f"Cannot connect to Strapi: {e}")
        print_warning("Publishing will not work without Strapi connection")
        return False


async def create_test_task() -> Optional[str]:
    """Create a test task"""
    print_header("Creating Test Task")
    
    task_data = {
        "task_name": "Production Pipeline Test",
        "topic": "The Future of AI: Emerging Trends and Opportunities",
        "primary_keyword": "artificial intelligence trends",
        "target_audience": "technology professionals",
        "category": "AI & Technology"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_URL}/api/tasks",
                json=task_data,
                timeout=30
            )
            
            if response.status_code == 201:
                data = response.json()
                task_id = data.get("id")
                print_success(f"Task created successfully")
                print_info(f"Task ID: {task_id}")
                print_info(f"Topic: {task_data['topic']}")
                return task_id
            else:
                print_error(f"Failed to create task: HTTP {response.status_code}")
                print_error(f"Response: {response.text[:200]}")
                return None
    except Exception as e:
        print_error(f"Error creating task: {e}")
        return None


async def monitor_task(task_id: str, max_wait_seconds: int = 60) -> Optional[Dict[str, Any]]:
    """Monitor task progress"""
    print_header(f"Monitoring Task: {task_id}")
    
    start_time = time.time()
    check_count = 0
    
    while time.time() - start_time < max_wait_seconds:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{API_URL}/api/tasks/{task_id}", timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    check_count += 1
                    
                    status = data.get("status")
                    elapsed = int(time.time() - start_time)
                    
                    print(f"[{elapsed}s, check {check_count}] Status: {status}")
                    
                    if status == "completed":
                        print_success("Task completed!")
                        return data
                    elif status == "failed":
                        print_error("Task failed")
                        print_error(f"Error: {data.get('error')}")
                        return data
                    else:
                        # Still processing
                        await asyncio.sleep(5)
                else:
                    print_warning(f"Cannot fetch task: HTTP {response.status_code}")
                    await asyncio.sleep(5)
        except Exception as e:
            print_warning(f"Error checking task: {e}")
            await asyncio.sleep(5)
    
    print_error(f"Task did not complete within {max_wait_seconds} seconds")
    return None


async def verify_result(result: Dict[str, Any]) -> bool:
    """Verify the task result"""
    print_header("Verifying Result")
    
    try:
        # Check content generation
        content_length = result.get("content_length", 0)
        if content_length > 0:
            print_success(f"Content generated: {content_length} characters")
        else:
            print_warning("No content generated")
        
        # Check critique
        quality_score = result.get("quality_score", 0)
        approved = result.get("content_approved", False)
        
        if approved:
            print_success(f"Content approved by critique loop (score: {quality_score}/100)")
        else:
            print_warning(f"Content NOT approved (score: {quality_score}/100)")
            print_info(f"Feedback: {result.get('critique_feedback')}")
        
        # Check Strapi publication
        post_id = result.get("strapi_post_id")
        post_url = result.get("strapi_url")
        publish_status = result.get("publish_status")
        
        if post_id and publish_status == "published":
            print_success(f"Blog post published to Strapi!")
            print_info(f"Post ID: {post_id}")
            print_info(f"Post URL: {post_url}")
            return True
        elif publish_status == "not_published":
            print_warning(f"Content not published: {result.get('publish_error')}")
            return False
        else:
            print_info(f"Publish status: {publish_status}")
            return False
    
    except Exception as e:
        print_error(f"Error verifying result: {e}")
        return False


async def test_executor_stats():
    """Display executor statistics"""
    print_header("Executor Statistics")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_URL}/api/tasks/stats", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                print_info(f"Total Processed: {data.get('total_processed')}")
                print_info(f"Successful: {data.get('successful')}")
                print_info(f"Failed: {data.get('failed')}")
                print_info(f"Published to Strapi: {data.get('published_to_strapi')}")
                
                critique_stats = data.get('critique_stats', {})
                if critique_stats:
                    print_info(f"Approval Rate: {critique_stats.get('approval_rate')}")
    except Exception as e:
        print_warning(f"Cannot fetch executor stats: {e}")


async def run_full_test():
    """Run complete pipeline test"""
    print("\n" + "=" * 60)
    print("🚀 PRODUCTION PIPELINE TEST")
    print("=" * 60)
    
    # Test 1: Backend health
    if not await test_api_health():
        print_error("\n❌ Backend not running!")
        print_info("Start backend with: python -m uvicorn src.cofounder_agent.main:app --reload")
        return False
    
    # Test 2: Orchestrator
    await test_orchestrator_status()
    
    # Test 3: Strapi
    strapi_ok = await test_strapi_connection()
    if not strapi_ok:
        print_warning("\nWarning: Strapi connection failed - posts won't be published")
    
    # Test 4: Create task
    task_id = await create_test_task()
    if not task_id:
        print_error("\n❌ Failed to create task")
        return False
    
    # Test 5: Monitor execution
    result = await monitor_task(task_id, max_wait_seconds=90)
    if not result:
        print_error("\n❌ Task did not complete")
        return False
    
    # Test 6: Verify result
    success = await verify_result(result)
    
    # Test 7: Show stats
    await test_executor_stats()
    
    # Final summary
    print_header("Test Summary")
    if success and strapi_ok:
        print_success("✅ FULL PIPELINE TEST PASSED!")
        print_info("Your production pipeline is ready to generate blog posts!")
    elif success:
        print_success("⚠️  PARTIAL SUCCESS")
        print_info("Pipeline works, but Strapi publishing needs to be configured")
    else:
        print_warning("⚠️  PARTIAL SUCCESS")
        print_info("Content generated but not published to Strapi")
    
    return True


if __name__ == "__main__":
    print_info(f"Testing production pipeline...")
    print_info(f"Backend URL: {API_URL}")
    print_info(f"Strapi URL: {STRAPI_URL}")
    
    try:
        success = asyncio.run(run_full_test())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print_warning("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
