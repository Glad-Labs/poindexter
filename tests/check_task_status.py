#!/usr/bin/env python3
"""Check the status of a specific task in the database"""

import asyncio
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'cofounder_agent'))

from src.cofounder_agent.services.database_service import DatabaseService

async def main():
    db = DatabaseService()
    try:
        # Check the task that was just approved
        task_id = '8d9fca0c-0945-42a0-aee1-ba824dee75d1'
        print(f"Checking task {task_id}...")
        
        task = await db.get_task(task_id)
        
        if task:
            print("\n✅ TASK FOUND IN DATABASE")
            print(f"   ID: {task.get('id')}")
            print(f"   Status: {task.get('status')}")
            print(f"   Updated At: {task.get('updated_at')}")
            metadata = task.get('task_metadata', {})
            if metadata:
                print(f"   Metadata Keys: {list(metadata.keys())}")
                if 'approval_feedback' in metadata:
                    print(f"   Approval Feedback: {metadata.get('approval_feedback')}")
                if 'reviewer_notes' in metadata:
                    print(f"   Reviewer Notes: {metadata.get('reviewer_notes')}")
        else:
            print(f"\n❌ Task not found")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db.close()

if __name__ == '__main__':
    asyncio.run(main())
