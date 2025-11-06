#!/usr/bin/env python
"""
Simple test script to verify the end-to-end task pipeline:
1. Create a task
2. Monitor its progress
3. Verify completion

Run this AFTER starting the backend:
    python run.py  (in src/cofounder_agent/)

Then in another terminal:
    python test_task_pipeline.py
"""

import requests
import json
import time
import sys

# Configuration
API_BASE_URL = "http://127.0.0.1:8001"
POLL_INTERVAL = 1  # Check status every 1 second
MAX_WAIT_TIME = 15  # Wait max 15 seconds for completion

def print_header(text):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")

def print_success(text):
    """Print success message"""
    print(f"✅  {text}")

def print_error(text):
    """Print error message"""
    print(f"❌  {text}")

def print_info(text):
    """Print info message"""
    print(f"ℹ️   {text}")

def check_backend_health():
    """Verify backend is running"""
    print_info(f"Checking backend health at {API_BASE_URL}...")
    try:
        response = requests.get(f"{API_BASE_URL}/api/health", timeout=2)
        if response.status_code == 200:
            print_success(f"Backend is healthy: {response.json()}")
            return True
        else:
            print_error(f"Backend returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_error(f"Cannot connect to backend at {API_BASE_URL}")
        print_info("Make sure to start the backend first:")
        print_info("  cd src/cofounder_agent")
        print_info("  python run.py")
        return False
    except Exception as e:
        print_error(f"Error checking health: {e}")
        return False

def create_test_task():
    """Create a test task"""
    print_header("STEP 1: Creating Test Task")
    
    task_data = {
        "task_name": "Test Pipeline Task",
        "topic": "The Future of AI in 2025",
        "primary_keyword": "AI future",
        "target_audience": "Tech professionals",
        "category": "technology"
    }
    
    print_info(f"Creating task with data:")
    print(json.dumps(task_data, indent=2))
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/tasks",
            json=task_data,
            timeout=5
        )
        
        if response.status_code == 201:
            task = response.json()
            task_id = task.get("id")
            print_success(f"Task created successfully!")
            print(f"  Task ID: {task_id}")
            print(f"  Status: {task.get('status')}")
            print(f"  Created: {task.get('created_at')}")
            return task_id
        else:
            print_error(f"Failed to create task: {response.status_code}")
            print(f"  Response: {response.text}")
            return None
    except Exception as e:
        print_error(f"Error creating task: {e}")
        return None

def monitor_task_progress(task_id, max_wait=MAX_WAIT_TIME):
    """Monitor task progress until completion"""
    print_header("STEP 2: Monitoring Task Progress")
    
    print_info(f"Polling task status every {POLL_INTERVAL} second(s)...")
    print_info(f"Maximum wait time: {max_wait} seconds\n")
    
    start_time = time.time()
    last_status = None
    
    while True:
        elapsed = time.time() - start_time
        
        try:
            response = requests.get(
                f"{API_BASE_URL}/api/tasks/{task_id}",
                timeout=5
            )
            
            if response.status_code == 200:
                task = response.json()
                current_status = task.get("status")
                
                # Print status update if changed
                if current_status != last_status:
                    print_info(f"[{elapsed:.1f}s] Status: {current_status}")
                    last_status = current_status
                    
                    # Print additional info based on status
                    if current_status == "in_progress":
                        print_info(f"  Task is being executed by background processor...")
                    elif current_status == "completed":
                        print_success(f"Task completed!")
                        return task
                    elif current_status == "failed":
                        print_error(f"Task failed: {task.get('result')}")
                        return task
                
                # Check timeout
                if elapsed > max_wait:
                    print_error(f"Task did not complete within {max_wait} seconds")
                    print_info(f"Final status: {current_status}")
                    return task
                
                # Small delay before next poll
                time.sleep(POLL_INTERVAL)
                
            else:
                print_error(f"Error fetching task status: {response.status_code}")
                return None
                
        except Exception as e:
            print_error(f"Error monitoring task: {e}")
            return None

def display_task_details(task):
    """Display completed task details"""
    print_header("STEP 3: Task Completion Details")
    
    print(f"Task ID:     {task.get('id')}")
    print(f"Task Name:   {task.get('task_name')}")
    print(f"Status:      {task.get('status')}")
    print(f"Created:     {task.get('created_at')}")
    print(f"Updated:     {task.get('updated_at')}")
    
    if task.get('result'):
        print(f"\nTask Result:")
        print("-" * 60)
        result = task.get('result')
        if isinstance(result, str):
            # Try to parse as JSON for pretty printing
            try:
                result_json = json.loads(result)
                print(json.dumps(result_json, indent=2))
            except:
                print(result)
        else:
            print(json.dumps(result, indent=2))
    else:
        print("\n⚠️  No result available yet")

def check_executor_stats():
    """Check task executor statistics"""
    print_header("Task Executor Statistics")
    
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/metrics",
            timeout=5
        )
        
        if response.status_code == 200:
            metrics = response.json()
            print(json.dumps(metrics, indent=2))
        else:
            print_info(f"Metrics endpoint status: {response.status_code}")
    except Exception as e:
        print_info(f"Could not fetch metrics: {e}")

def main():
    """Main test flow"""
    print("\n" + "="*60)
    print("  📋 GLAD Labs Task Pipeline End-to-End Test")
    print("="*60)
    
    # Step 1: Check health
    if not check_backend_health():
        print_error("Backend is not running. Exiting.")
        sys.exit(1)
    
    # Step 2: Create task
    task_id = create_test_task()
    if not task_id:
        print_error("Failed to create task. Exiting.")
        sys.exit(1)
    
    # Step 3: Monitor progress
    completed_task = monitor_task_progress(task_id)
    if not completed_task:
        print_error("Failed to monitor task. Exiting.")
        sys.exit(1)
    
    # Step 4: Display results
    display_task_details(completed_task)
    
    # Step 5: Show metrics
    check_executor_stats()
    
    # Final summary
    print_header("✨ Test Complete!")
    
    if completed_task.get('status') == 'completed':
        print_success("Task pipeline is working correctly!")
        print_info("Background executor successfully:")
        print_info("  1. Detected pending task")
        print_info("  2. Updated status to 'in_progress'")
        print_info("  3. Executed task")
        print_info("  4. Stored results")
        print_info("  5. Updated status to 'completed'")
    else:
        print_info(f"Task final status: {completed_task.get('status')}")
    
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    main()
