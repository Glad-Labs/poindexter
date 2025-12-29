#!/usr/bin/env python3
"""
Test Script: Create a Real Task with Constraint Compliance

This script demonstrates the proper way to test the ConstraintComplianceDisplay
component by creating a real task through the backend API that generates actual
constraint compliance data.

Usage:
    python test_constraint_compliance.py

Requirements:
    - Backend running on http://localhost:8000
    - PostgreSQL database configured
    - Python requests library: pip install requests
"""

import requests
import json
import time
import sys
from datetime import datetime

# Configuration
BACKEND_URL = "http://localhost:8000"
# Use the dev JWT token (valid for testing)
JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtdXNlciIsImlhdCI6MTcwMjc0NzIwMCwianRpIjoiZGV2LXRva2VuIiwidHlwZSI6ImFjY2VzcyJ9.Y8J_2F7G5H4K9L0M1N2O3P4Q5R6S7T8U9V0W1X2Y3Z4"

def get_headers():
    """Return headers with JWT token"""
    return {
        "Authorization": f"Bearer {JWT_TOKEN}",
        "Content-Type": "application/json"
    }

def create_task_with_constraints():
    """Create a new task with content constraints"""
    print("=" * 70)
    print("STEP 1: Creating Task with Content Constraints")
    print("=" * 70)
    
    payload = {
        "task_name": "Test Constraint Compliance - AI Marketing 2025",
        "topic": "How Artificial Intelligence is Revolutionizing Digital Marketing in 2025",
        "category": "marketing",
        "primary_keyword": "AI digital marketing",
        "target_audience": "Digital marketing professionals and business leaders",
        "content_constraints": {
            "target_word_count": 800,
            "word_count_tolerance": 10,
            "writing_style": "professional",
            "strict_mode": True
        }
    }
    
    print(f"\n📤 Sending POST /api/tasks with constraints:")
    print(json.dumps(payload, indent=2))
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/tasks",
            headers=get_headers(),
            json=payload,
            timeout=10
        )
        
        if response.status_code != 201:
            print(f"\n❌ Failed to create task: {response.status_code}")
            print(response.text)
            return None
        
        task_data = response.json()
        task_id = task_data.get("id")
        print(f"\n✅ Task created successfully!")
        print(f"   Task ID: {task_id}")
        print(f"   Status: {task_data.get('status')}")
        
        return task_id
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Request failed: {e}")
        return None

def monitor_task_completion(task_id, max_wait=120):
    """Monitor task status until completion"""
    print("\n" + "=" * 70)
    print("STEP 2: Monitoring Task Progress")
    print("=" * 70)
    
    start_time = time.time()
    poll_interval = 3  # seconds
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(
                f"{BACKEND_URL}/api/tasks/{task_id}",
                headers=get_headers(),
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"\n⚠️  Could not fetch task status: {response.status_code}")
                time.sleep(poll_interval)
                continue
            
            task = response.json()
            status = task.get("status")
            elapsed = time.time() - start_time
            
            # Display status update
            message = task.get("task_metadata", {}).get("message", "")
            percentage = task.get("task_metadata", {}).get("percentage", 0)
            
            status_symbol = {
                "pending": "⏳",
                "running": "⚙️",
                "completed": "✅",
                "failed": "❌"
            }.get(status, "❓")
            
            print(f"{status_symbol} [{elapsed:5.0f}s] Status: {status:10s} ({percentage}%) {message}")
            
            # Check if completed or failed
            if status in ["completed", "failed", "approved"]:
                return task, status
            
            time.sleep(poll_interval)
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Request error: {e}")
            time.sleep(poll_interval)
    
    print(f"\n❌ Task did not complete within {max_wait} seconds")
    return None, "timeout"

def extract_compliance_data(task):
    """Extract and display constraint compliance data"""
    print("\n" + "=" * 70)
    print("STEP 3: Extracting Constraint Compliance Data")
    print("=" * 70)
    
    # Try to get compliance data from multiple sources
    compliance = (
        task.get("constraint_compliance") or  # Top-level (extracted by API)
        task.get("task_metadata", {}).get("constraint_compliance")  # From metadata
    )
    
    if not compliance:
        print("\n⚠️  No constraint_compliance data found in task")
        print("\nAvailable keys in task response:")
        for key in task.keys():
            print(f"  - {key}")
        return None
    
    print("\n✅ Constraint Compliance Data Found!")
    print("\nCompliance Metrics:")
    print(json.dumps(compliance, indent=2))
    
    return compliance

def validate_compliance_structure(compliance):
    """Validate that compliance data has required fields"""
    print("\n" + "=" * 70)
    print("STEP 4: Validating Compliance Data Structure")
    print("=" * 70)
    
    required_fields = [
        "word_count_actual",
        "word_count_target",
        "word_count_within_tolerance",
        "word_count_percentage",
        "writing_style",
        "strict_mode_enforced",
        "compliance_status"
    ]
    
    print("\nRequired Fields Check:")
    all_present = True
    for field in required_fields:
        present = field in compliance
        symbol = "✅" if present else "❌"
        print(f"{symbol} {field:35s}: {compliance.get(field, 'MISSING')}")
        if not present:
            all_present = False
    
    if all_present:
        print("\n✅ All required fields present!")
        return True
    else:
        print("\n⚠️  Some required fields are missing!")
        return False

def verify_frontend_display(task_id):
    """Provide instructions for verifying in frontend"""
    print("\n" + "=" * 70)
    print("STEP 5: Verifying Display in Frontend")
    print("=" * 70)
    
    print(f"""
To verify the ConstraintComplianceDisplay component in the frontend:

1. Open Oversight Hub: http://localhost:3001
2. Log in (if needed)
3. Go to the "Tasks" tab
4. Search for or find task ID: {task_id}
5. Click on the task to open the detail modal
6. Look for the "Constraint Compliance" section

Expected to see:
  ✓ Word count progress bar (shows ~800/800)
  ✓ Writing style indicator (shows "professional")
  ✓ Strict mode status (shows "ON")
  ✓ Variance percentage (shows something like "-0.625%")
  ✓ Compliance status badge (green for "compliant")

If you don't see the section:
  1. Check browser console for errors (F12)
  2. Verify task_id is correct: {task_id}
  3. Refresh the page (Ctrl+R)
  4. Check backend logs for any errors
""")

def main():
    """Main test flow"""
    print("\n🧪 CONSTRAINT COMPLIANCE DISPLAY TEST")
    print("   Creating real task with compliance data\n")
    
    # Step 1: Create task
    task_id = create_task_with_constraints()
    if not task_id:
        print("\n❌ Failed to create task. Exiting.")
        sys.exit(1)
    
    # Step 2: Monitor completion
    print(f"\n⏳ Waiting for task to complete (max 120 seconds)...")
    task, final_status = monitor_task_completion(task_id)
    
    if not task:
        print("\n❌ Task monitoring timed out. Exiting.")
        sys.exit(1)
    
    if final_status == "failed":
        print("\n❌ Task failed during execution")
        print("Error details:", task.get("error_message", "Unknown error"))
        sys.exit(1)
    
    # Step 3: Extract compliance
    compliance = extract_compliance_data(task)
    if not compliance:
        print("\n⚠️  Could not find compliance data (task may not have included constraints)")
        sys.exit(1)
    
    # Step 4: Validate structure
    is_valid = validate_compliance_structure(compliance)
    
    # Step 5: Frontend instructions
    verify_frontend_display(task_id)
    
    # Summary
    print("\n" + "=" * 70)
    print("✅ TEST COMPLETE")
    print("=" * 70)
    print(f"""
Task ID: {task_id}
Status: {final_status}
Compliance Valid: {is_valid}

Next: Visit http://localhost:3001 to verify the display in Oversight Hub
""")

if __name__ == "__main__":
    main()
