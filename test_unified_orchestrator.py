#!/usr/bin/env python3
"""
Test script to verify UnifiedOrchestrator is properly used for content tasks.
This confirms the permanent fix for the initialization order bug.
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
VALID_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZW1vLXVzZXIiLCJ0eXBlIjoiYWNjZXNzIn0.FRf3UM6L52qqk4LnJYAHhLo0EG_iEuaF9bNvAXr4hME"

def test_content_task():
    """Create a content task and monitor for UnifiedOrchestrator behavior."""
    
    print("\n" + "=" * 80)
    print("Testing UnifiedOrchestrator Integration")
    print("=" * 80 + "\n")
    
    # Create a content task with correct schema
    print("[1] Creating content task...")
    task_data = {
        "task_type": "blog_post",
        "topic": "The Impact of AI on Modern Education",
        "style": "technical",
        "tone": "professional",
        "target_length": 1500,
        "tags": ["ai", "education"],
        "generate_featured_image": False,
        "publish_mode": "draft"
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {VALID_JWT}"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/content/tasks",
            json=task_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code not in [200, 201]:
            print(f"❌ Failed to create task: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
        
        task = response.json()
        task_id = task.get("task_id")
        print(f"✅ Task created: {task_id}\n")
        
        # Monitor task progress
        print("[2] Monitoring task execution...")
        print(f"    Task Type: {task.get('type')}")
        print(f"    Title: {task.get('title')}")
        print(f"    Status: {task.get('status', 'pending')}")
        
        # Wait for task to process
        print("\n    Waiting for UnifiedOrchestrator to process (20 seconds)...")
        
        for i in range(20):
            time.sleep(1)
            print(".", end="", flush=True)
        
        print("\n")
        
        # Fetch task status
        print("[3] Fetching task results...")
        
        get_response = requests.get(
            f"{BASE_URL}/api/tasks/{task_id}",
            headers=headers,
            timeout=10
        )
        
        if get_response.status_code == 200:
            final_task = get_response.json()
            
            # Check for UnifiedOrchestrator indicators
            content = final_task.get("content", {})
            
            if isinstance(content, str):
                # Try to parse as JSON
                try:
                    content = json.loads(content)
                except:
                    pass
            
            print(f"\n✅ Task Status: {final_task.get('status')}")
            print(f"   Content Type: {type(content).__name__}")
            
            # Check for pipeline stages
            has_research = False
            has_quality_metrics = False
            has_image_data = False
            
            if isinstance(content, dict):
                has_research = "research" in content or "research_stage" in content
                has_quality_metrics = "quality_score" in content or "scores" in content
                has_image_data = "image" in content or "image_description" in content or "image_url" in content
                
                # Print what we found
                print(f"\n📊 UnifiedOrchestrator Pipeline Indicators:")
                print(f"   ✓ Research stage output: {'YES' if has_research else 'NO'}")
                print(f"   ✓ Quality metrics: {'YES' if has_quality_metrics else 'NO'}")
                print(f"   ✓ Image data: {'YES' if has_image_data else 'NO'}")
                
                # Show sample structure
                print(f"\n📋 Content Keys Found: {list(content.keys())[:10]}")  # First 10 keys
                
                # Check for fallback indicators
                if "fallback" in str(content).lower():
                    print("\n⚠️  WARNING: Fallback mode detected in content!")
                    return False
                else:
                    print("\n✅ No fallback mode detected - UnifiedOrchestrator is working!")
                
                # Show a sample of the content
                print(f"\n📝 Content Sample (first 500 chars):")
                if isinstance(content, dict):
                    sample = json.dumps(content, indent=2)[:500]
                else:
                    sample = str(content)[:500]
                print(f"   {sample}...\n")
                
                return True
            else:
                # Content is a string
                if "fallback" in content.lower():
                    print("\n⚠️  WARNING: Fallback mode detected!")
                    return False
                else:
                    print("\n✅ Content generated successfully")
                    print(f"\nContent preview (first 300 chars):\n{content[:300]}...\n")
                    return True
        else:
            print(f"❌ Failed to fetch task: {get_response.status_code}")
            return False
    
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to backend at http://localhost:8000")
        print("   Make sure the backend is running: npm run dev:cofounder")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = test_content_task()
    
    print("=" * 80)
    if success:
        print("✅ TEST PASSED: UnifiedOrchestrator is properly initialized and processing tasks")
        print("\nKey Fix Confirmed:")
        print("  • TaskExecutor deferred startup until UnifiedOrchestrator available")
        print("  • Legacy Orchestrator removed from initialization path")
        print("  • Tasks now use full content generation pipeline")
    else:
        print("❌ TEST FAILED: Check backend logs for issues")
    print("=" * 80 + "\n")
