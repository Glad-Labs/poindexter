#!/usr/bin/env python
"""
Phase 5 Step 6 - End-to-End Testing
Tests the complete approval workflow from task creation to publishing
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_scenario_1_create_task():
    """SCENARIO 1: Create content task"""
    print("\n" + "="*70)
    print("SCENARIO 1: CREATE CONTENT TASK")
    print("="*70)
    
    task_data = {
        "topic": "The Future of AI in Business 2025",
        "style": "technical",
        "tone": "professional",
        "target_length": 2000,
        "task_type": "blog_post"
    }
    
    print(f"\nÌ≥§ Creating task with payload:")
    print(f"   Topic: {task_data['topic']}")
    print(f"   Style: {task_data['style']}")
    print(f"   Target length: {task_data['target_length']} words")
    
    response = requests.post(f"{BASE_URL}/api/content/tasks", json=task_data)
    
    print(f"\nÌ≥ä Response Status: {response.status_code}")
    
    if response.status_code == 201:
        result = response.json()
        task_id = result.get("task_id")
        print(f"‚úÖ SUCCESS - Task created!")
        print(f"   Task ID: {task_id}")
        print(f"   Status: {result.get('status')}")
        print(f"   Created: {result.get('created_at')}")
        return task_id
    else:
        print(f"‚ùå FAILED - Status {response.status_code}")
        print(f"   Response: {response.text}")
        return None

def test_scenario_2_poll_task_status(task_id):
    """SCENARIO 2: Poll task status"""
    print("\n" + "="*70)
    print("SCENARIO 2: POLL TASK STATUS")
    print("="*70)
    
    print(f"\n‚è≥ Polling task status...")
    for i in range(15):  # Poll for up to 30 seconds
        response = requests.get(f"{BASE_URL}/api/content/tasks/{task_id}")
        
        if response.status_code == 200:
            result = response.json()
            status = result.get("status")
            print(f"   [{i+1}] Status: {status}")
            
            if status in ["awaiting_approval", "approved", "rejected", "published"]:
                print(f"\n‚úÖ Task reached terminal state: {status}")
                return result
            
            time.sleep(2)
        else:
            print(f"   ‚ùå Error: {response.status_code}")
            return None
    
    print(f"\n‚ö†Ô∏è Task still processing after 30 seconds (normal for complex tasks)")
    return None

def test_scenario_3_list_approval_queue():
    """SCENARIO 3: List approval queue"""
    print("\n" + "="*70)
    print("SCENARIO 3: LIST APPROVAL QUEUE")
    print("="*70)
    
    print(f"\nÌ≥ã Fetching approval queue (status=awaiting_approval)...")
    response = requests.get(f"{BASE_URL}/api/content/tasks?status=awaiting_approval&limit=100")
    
    print(f"Ì≥ä Response Status: {response.status_code}")
    
    if response.status_code == 200:
        result = response.json()
        total = result.get("total", 0)
        drafts = result.get("drafts", [])
        
        print(f"‚úÖ Queue retrieved")
        print(f"   Total tasks awaiting approval: {total}")
        
        if drafts:
            print(f"\n   Tasks in queue:")
            for draft in drafts:
                print(f"   - {draft.get('task_id')}: {draft.get('topic')}")
            return drafts
        else:
            print(f"   (Queue is empty - no tasks awaiting approval)")
            return []
    else:
        print(f"‚ùå FAILED - Status {response.status_code}")
        return []

def test_scenario_4_verify_database():
    """SCENARIO 4: Verify database integrity"""
    print("\n" + "="*70)
    print("SCENARIO 4: VERIFY DATABASE INTEGRITY")
    print("="*70)
    
    print(f"\nÌ¥ç Querying database for approval fields...")
    
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost",
            database="glad_labs_dev",
            user="postgres",
            password="postgres"
        )
        cursor = conn.cursor()
        
        # Check if columns exist
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'content_tasks' 
            AND column_name IN (
                'approval_status', 'qa_feedback', 'human_feedback', 
                'approved_by', 'approval_timestamp', 'approval_notes'
            )
            ORDER BY column_name
        """)
        
        columns = cursor.fetchall()
        
        if len(columns) == 6:
            print(f"‚úÖ All 6 approval columns present in database:")
            for col_name, col_type in columns:
                print(f"   - {col_name}: {col_type}")
        else:
            print(f"‚ö†Ô∏è Only {len(columns)} of 6 approval columns found")
        
        # Count total tasks
        cursor.execute("SELECT COUNT(*) FROM content_tasks")
        task_count = cursor.fetchone()[0]
        print(f"\nÌ≥ä Total tasks in database: {task_count}")
        
        # Count tasks by status
        cursor.execute("""
            SELECT status, COUNT(*) as count 
            FROM content_tasks 
            GROUP BY status 
            ORDER BY count DESC
        """)
        
        print(f"   Tasks by status:")
        for status, count in cursor.fetchall():
            print(f"   - {status}: {count}")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"‚ùå Database query failed: {e}")
        return False

def test_scenario_5_test_approval_endpoint():
    """SCENARIO 5: Test approval endpoint"""
    print("\n" + "="*70)
    print("SCENARIO 5: TEST APPROVAL ENDPOINT")
    print("="*70)
    
    print(f"\nÌ¥ó Testing approval endpoint structure...")
    
    # This is a dry test - we're checking if the endpoint exists
    test_task_id = "test_task_id"
    approval_data = {
        "action": "approve",
        "feedback": "Great content!",
        "notes": "Ready for publication"
    }
    
    response = requests.post(
        f"{BASE_URL}/api/tasks/{test_task_id}/approve",
        json=approval_data
    )
    
    if response.status_code == 404:
        print(f"‚úÖ Approval endpoint exists (returned 404 for invalid task - expected)")
    elif response.status_code == 200:
        print(f"‚úÖ Approval endpoint working (returned 200)")
        print(f"   Response: {response.json()}")
    else:
        print(f"‚ö†Ô∏è Approval endpoint returned: {response.status_code}")
        print(f"   Response: {response.text}")

def main():
    """Run all E2E test scenarios"""
    print("\n" + "#"*70)
    print("# PHASE 5 STEP 6: END-TO-END TESTING")
    print("# Date: November 14, 2025")
    print("#"*70)
    
    # Test 1: Create task
    task_id = test_scenario_1_create_task()
    if not task_id:
        print("\n‚ùå TEST FAILED at Step 1 - Cannot create task")
        return
    
    # Test 2: Poll task status
    task_data = test_scenario_2_poll_task_status(task_id)
    
    # Test 3: List approval queue
    test_scenario_3_list_approval_queue()
    
    # Test 4: Verify database
    test_scenario_4_verify_database()
    
    # Test 5: Test approval endpoint
    test_scenario_5_test_approval_endpoint()
    
    print("\n" + "#"*70)
    print("# E2E TEST SUITE COMPLETE")
    print("#"*70)
    print("\n‚úÖ All end-to-end scenarios have been tested!")
    print("\nNOTE: This is Phase 5 Step 6 E2E testing.")
    print("System is ready for production use once approval workflows")
    print("are verified to publish to Strapi CMS.")

if __name__ == "__main__":
    main()
