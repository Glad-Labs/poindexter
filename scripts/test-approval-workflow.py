#!/usr/bin/env python3
"""
Test script for approval workflow with database verification.

This script:
1. Creates a test task
2. Moves it to awaiting_approval status
3. Approves it with feedback
4. Verifies database persistence
5. Checks all fields are updated correctly
"""

import asyncio
import aiohttp
import asyncpg
import json
import os
from datetime import datetime
from pathlib import Path
import sys
import uuid

# Load environment from .env.local
def load_env():
    env_file = Path(__file__).parent.parent / ".env.local"
    if env_file.exists():
        try:
            with open(env_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ.setdefault(key.strip(), value.strip())
        except Exception as e:
            print(f"Warning: Could not load .env.local: {e}")

load_env()

# Configuration
API_URL = "http://localhost:8000"
DB_URL = os.getenv("DATABASE_URL")

# For testing without auth, we'll use the database directly to create tasks
async def create_test_task_in_db(pool):
    """Create a test task directly in the database"""
    print(f"   Creating task directly in database...")
    
    async with pool.acquire() as conn:
        task_uuid = str(uuid.uuid4())
        task = await conn.fetchrow("""
            INSERT INTO content_tasks (task_id, title, category, description, status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, task_id, title, status
        """, 
        task_uuid,
        f"Test Approval Task {datetime.now().isoformat()}",
        "testing",
        "Testing approval workflow with logging",
        "pending",
        datetime.utcnow(),
        datetime.utcnow()
        )
        
        task_id = str(task['task_id']) if task['task_id'] else str(task['id'])
        print(f"   [SUCCESS] Task created in DB: {task_id}")
        print(f"   Status: {task['status']}")
        return task_id

async def main():
    print(f"\n{'='*80}")
    print(f"[TEST] APPROVAL WORKFLOW TEST")
    print(f"{'='*80}\n")

    if not DB_URL:
        print("FAILED: DATABASE_URL not set in environment")
        return

    # Connect to database
    print("[INFO] Connecting to database...")
    try:
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=10)
        db_name = DB_URL.split('/')[-1] if DB_URL else "unknown"
        print(f"[SUCCESS] Connected to {db_name} database\n")
    except Exception as e:
        print(f"[FAILED] Failed to connect to database: {e}")
        print(f"[DEBUG] DATABASE_URL: {DB_URL}")
        return

    task_id = None
    
    try:
        # Step 1: Create test task (direct DB since API needs auth)
        print("[STEP 1] Creating test task...")
        task_id = await create_test_task_in_db(pool)

        # Step 2: Get task to verify it exists
        print(f"\n[STEP 2] Retrieving task from database...")
        async with pool.acquire() as conn:
            task = await conn.fetchrow(
                "SELECT * FROM content_tasks WHERE id = $1 OR task_id = $2",
                int(task_id) if task_id.isdigit() else None,
                task_id,
            )
            if task:
                print(f"[SUCCESS] Task found in DB")
                print(f"   - ID: {task['id']}")
                print(f"   - Task ID: {task['task_id']}")
                print(f"   - Status: {task['status']}")
                print(f"   - Title: {task['title']}")
            else:
                print(f"[FAILED] Task not found in database")
                return

        # Step 3: Move to awaiting_approval status
        print(f"\n[STEP 3] Transitioning to awaiting_approval status...")
        print(f"   PUT {API_URL}/api/tasks/{{task_id}}/status/validated")

        payload = {
            "status": "awaiting_approval",
            "reason": "Ready for approval",
            "metadata": {"action": "test_workflow", "timestamp": datetime.now().isoformat()},
        }
        print(f"   Payload: {json.dumps(payload, indent=2)}")

        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{API_URL}/api/tasks/{task_id}/status/validated",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"[SUCCESS] Status updated")
                    print(f"   Response: {json.dumps(data, indent=2)}")
                else:
                    print(f"[FAILED] {resp.status}")
                    print(await resp.text())

        # Verify DB state after status change
        print(f"\n[INFO] Verifying DB after status change...")
        async with pool.acquire() as conn:
            task = await conn.fetchrow(
                "SELECT id, status, task_metadata, updated_at FROM content_tasks WHERE id = $1 OR task_id = $2",
                int(task_id) if task_id.isdigit() else None,
                task_id,
            )
            if task:
                print(f"[SUCCESS] Task updated")
                print(f"   - Status: {task['status']}")
                print(f"   - Updated at: {task['updated_at']}")
                print(f"   - Metadata keys: {list(json.loads(task['task_metadata']).keys()) if task['task_metadata'] else 'None'}")

        # Step 4: Approve with feedback
        print(f"\n[STEP 4] Approving task with feedback...")
        print(f"   PUT {API_URL}/api/tasks/{{task_id}}/status/validated")

        approval_feedback = "Great content! Minor grammar fix needed but overall excellent work."
        payload = {
            "status": "approved",
            "reason": "Content approved by reviewer",
            "metadata": {
                "action": "approval",
                "approval_feedback": approval_feedback,
                "reviewer_notes": "Very good quality",
                "approved_at": datetime.now().isoformat(),
            },
        }
        print(f"   Payload: {json.dumps(payload, indent=2)}")

        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{API_URL}/api/tasks/{task_id}/status/validated",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"[SUCCESS] Approval submitted")
                    print(f"   Response: {json.dumps(data, indent=2)}")
                else:
                    print(f"[FAILED] {resp.status}")
                    print(await resp.text())

        # Step 5: Verify database persistence (CRITICAL)
        print(f"\n{'='*80}")
        print(f"[VERIFICATION] Checking Database Persistence")
        print(f"{'='*80}\n")

        async with pool.acquire() as conn:
            # Get complete task record
            task = await conn.fetchrow(
                """SELECT id, task_id, title, status, task_metadata, updated_at 
                   FROM content_tasks 
                   WHERE id = $1 OR task_id = $2
                   ORDER BY updated_at DESC LIMIT 1""",
                int(task_id) if task_id.isdigit() else None,
                task_id,
            )

            if task:
                print(f"[SUCCESS] Task found in database\n")
                
                # Status check
                print(f"[CHECK] Status:")
                print(f"   Expected: approved")
                print(f"   Actual: {task['status']}")
                status_ok = task['status'] == 'approved'
                print(f"   {'PASS' if status_ok else 'FAIL'}\n")

                # Metadata check
                print(f"[CHECK] Metadata:")
                metadata = json.loads(task['task_metadata']) if task['task_metadata'] else {}
                print(f"   Metadata keys: {list(metadata.keys())}")
                
                # Check for approval fields
                approval_fields = ['approval_feedback', 'reviewer_notes', 'approved_at', 'action']
                for field in approval_fields:
                    value = metadata.get(field)
                    status = 'OK' if value else 'MISSING'
                    print(f"   {status:8} {field}: {value}")

                metadata_ok = all(metadata.get(field) for field in approval_fields)
                print(f"   {'PASS' if metadata_ok else 'FAIL'}\n")

                # Updated timestamp check
                print(f"[CHECK] Timestamp:")
                print(f"   Updated at: {task['updated_at']}")
                timestamp_ok = task['updated_at'] is not None
                print(f"   {'PASS' if timestamp_ok else 'FAIL'}\n")

                # Overall result
                print(f"{'='*80}")
                all_ok = status_ok and metadata_ok and timestamp_ok
                if all_ok:
                    print(f"[RESULT] SUCCESS - Database persistence working correctly!")
                else:
                    print(f"[RESULT] FAILED - Database persistence issues detected!")
                print(f"{'='*80}\n")

                # Get status history
                print(f"[INFO] Status History:")
                history = await conn.fetch(
                    """SELECT old_status, new_status, reason, created_at 
                       FROM status_history 
                       WHERE task_id = $1 
                       ORDER BY created_at DESC
                       LIMIT 10""",
                    task_id,
                )
                if history:
                    for i, h in enumerate(history, 1):
                        print(f"   {i}. {h['old_status']} -> {h['new_status']} ({h['reason']}) at {h['created_at']}")
                else:
                    print(f"   No history found")

            else:
                print(f"[FAILED] Task not found in database!")
                print(f"   Searched for ID: {task_id}")

        print(f"\n[INFO] Test complete!\n")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
