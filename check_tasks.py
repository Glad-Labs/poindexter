#!/usr/bin/env python
"""Check task status from the backend API"""
import requests
import json
from datetime import datetime

try:
    print("🔍 Fetching tasks from backend...\n")
    response = requests.get('http://localhost:8000/api/tasks')
    
    if response.status_code == 200:
        data = response.json()
        
        print(f"✅ API Connected")
        print(f"📊 Total Tasks: {data.get('total', len(data.get('tasks', [])))}")
        print()
        
        tasks = data.get('tasks', [])
        if not tasks:
            print("⚠️  No tasks found yet")
        else:
            print("📋 Task List:\n")
            for i, task in enumerate(tasks, 1):
                task_id = task.get('id', 'N/A')[:8]
                task_name = task.get('task_name', 'Unnamed')
                status = task.get('status', 'unknown')
                created_at = task.get('created_at', 'N/A')
                result = task.get('result', {})
                
                # Status emoji
                status_emoji = {
                    'pending': '⏳',
                    'in_progress': '⚙️',
                    'completed': '✅',
                    'failed': '❌',
                    'queued': '📋'
                }.get(status, '❓')
                
                print(f"{i}. {status_emoji} {task_name}")
                print(f"   ID: {task_id}...")
                print(f"   Status: {status}")
                print(f"   Created: {created_at}")
                
                if result:
                    print(f"   Result: {json.dumps(result, indent=6)[:200]}...")
                print()
        
        print("\n📈 Metrics:")
        metrics_response = requests.get('http://localhost:8000/api/metrics')
        if metrics_response.status_code == 200:
            metrics = metrics_response.json()
            print(f"  - Total: {metrics.get('total_tasks', 0)}")
            print(f"  - Completed: {metrics.get('completed_tasks', 0)}")
            print(f"  - Failed: {metrics.get('failed_tasks', 0)}")
            print(f"  - Pending: {metrics.get('pending_tasks', 0)}")
            print(f"  - Success Rate: {metrics.get('success_rate', 0):.1f}%")
        
    else:
        print(f"❌ API Error: {response.status_code}")
        print(response.text)
        
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to backend at http://localhost:8000")
    print("   Make sure the Co-Founder Agent is running:")
    print("   python -m uvicorn src.cofounder_agent.main:app --reload")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
