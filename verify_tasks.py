#!/usr/bin/env python3
"""
Quick test to verify task executor is working and show output
Usage: python verify_tasks.py
"""

import sys
import json
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

print("=" * 70)
print("TASK PIPELINE VERIFICATION TOOL")
print("=" * 70)
print()

# Check if backend is running
print("🔍 Checking Backend Status...")
try:
    import requests
    response = requests.get("http://localhost:8000/api/health", timeout=2)
    if response.status_code == 200:
        print("✅ Backend is running")
        print(f"   Response: {response.json()}")
    else:
        print(f"❌ Backend returned status {response.status_code}")
except Exception as e:
    print(f"❌ Backend is NOT running: {e}")
    print()
    print("To start backend:")
    print("  cd src\\cofounder_agent")
    print("  python start_backend.py")
    sys.exit(1)

print()
print("=" * 70)
print("CREATING TEST TASK")
print("=" * 70)
print()

# Create a test task
task_data = {
    "task_name": "Verification Test",
    "topic": "Artificial Intelligence in Healthcare",
    "primary_keyword": "AI, healthcare, diagnosis",
    "target_audience": "Medical professionals",
    "category": "healthcare"
}

print(f"📝 Task Data:")
for key, value in task_data.items():
    print(f"   {key}: {value}")
print()

try:
    response = requests.post(
        "http://localhost:8000/api/tasks",
        json=task_data,
        timeout=5
    )
    
    if response.status_code == 201:
        result = response.json()
        task_id = result.get("id")
        print(f"✅ Task created successfully")
        print(f"   Task ID: {task_id}")
        print(f"   Status: {result.get('status')}")
    else:
        print(f"❌ Failed to create task: {response.status_code}")
        print(f"   Response: {response.text}")
        sys.exit(1)
except Exception as e:
    print(f"❌ Error creating task: {e}")
    sys.exit(1)

print()
print("=" * 70)
print("MONITORING TASK EXECUTION")
print("=" * 70)
print()

# Monitor task completion
start_time = time.time()
max_wait = 15
completed = False

print(f"⏳ Waiting up to {max_wait} seconds for task completion...")
print()

while time.time() - start_time < max_wait:
    try:
        response = requests.get(
            f"http://localhost:8000/api/tasks/{task_id}",
            timeout=5
        )
        
        if response.status_code == 200:
            result = response.json()
            status = result.get("status")
            elapsed = int(time.time() - start_time)
            
            print(f"   [{elapsed:2d}s] Status: {status}")
            
            if status == "completed":
                completed = True
                task_result = result
                break
            elif status == "failed":
                print(f"❌ Task failed!")
                print(f"   Error: {result.get('error')}")
                break
        else:
            print(f"   ⚠️  Error checking status: {response.status_code}")
            
    except Exception as e:
        print(f"   ⚠️  Error: {e}")
    
    time.sleep(1)

print()

if completed:
    print("=" * 70)
    print("✅ TASK COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print()
    
    print("📊 Task Results:")
    print()
    print(f"Task ID: {task_result.get('id')}")
    print(f"Task Name: {task_result.get('task_name')}")
    print(f"Topic: {task_result.get('topic')}")
    print(f"Status: {task_result.get('status')}")
    print(f"Created: {task_result.get('created_at')}")
    print(f"Completed: {task_result.get('completed_at')}")
    print()
    
    # Display generated content
    content = task_result.get("content", "")
    word_count = task_result.get("word_count", 0)
    
    print(f"📄 Generated Content ({word_count} words):")
    print()
    print("┌─ CONTENT START ─────────────────────────────────┐")
    print(content)
    print("└─ CONTENT END ───────────────────────────────────┘")
    print()
    
    print("=" * 70)
    print("✨ PIPELINE STATUS: FULLY FUNCTIONAL")
    print("=" * 70)
    print()
    print("What's Working:")
    print("  ✓ Task creation endpoint")
    print("  ✓ Background task processing (5s polling)")
    print("  ✓ Status updates")
    print("  ✓ Content generation (mock/placeholder)")
    print("  ✓ Database storage and retrieval")
    print()
    print("What's Currently Mock:")
    print("  • Content is using placeholder template")
    print("  • To get real content: integrate LLM (Ollama/OpenAI)")
    print()
    print("Next Steps:")
    print("  1. Review: UPGRADE_CONTENT_GENERATION.md")
    print("  2. Choose: Option 1 (Best), 2 (Easy), or 3 (Quick)")
    print("  3. Implement: Copy code from that section")
    print("  4. Test: Run this script again")
    print()
    
else:
    print("=" * 70)
    print("⚠️  TASK DID NOT COMPLETE")
    print("=" * 70)
    print()
    print("Possible reasons:")
    print("  1. TaskExecutor is not running")
    print("  2. Backend error (check terminal logs)")
    print("  3. Database connection issue")
    print()
    print("Debugging steps:")
    print("  1. Check backend terminal for errors")
    print("  2. Verify TaskExecutor is polling (should see log messages)")
    print("  3. Check database connectivity")
    print()
    
    # Try to get latest task info
    try:
        response = requests.get(f"http://localhost:8000/api/tasks/{task_id}")
        if response.status_code == 200:
            result = response.json()
            print(f"Last task status: {result.get('status')}")
            if result.get('error'):
                print(f"Error message: {result.get('error')}")
    except:
        pass

print()
