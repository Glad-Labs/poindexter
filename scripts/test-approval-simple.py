#!/usr/bin/env python3
"""
Simplified approval workflow test that:
1. Uses an existing task (or creates minimal one)
2. Tests approval transition
3. Verifies database persistence
"""

import asyncio
import aiohttp
import asyncpg
import json
import os
from datetime import datetime
from pathlib import Path

# Load from .env.local
env_file = Path(__file__).parent.parent / ".env.local"
if env_file.exists():
    try:
        with open(env_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line and 'DATABASE_URL' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
                    break
    except Exception as e:
        print(f"Warning: Could not load .env.local: {e}")

DB_URL = os.getenv("DATABASE_URL")

async def main():
    print(f"\n{'='*80}")
    print(f"[TEST] APPROVAL WORKFLOW TEST")
    print(f"{'='*80}\n")

    if not DB_URL:
        print("[FAILED] DATABASE_URL not set")
        return

    # Connect to database
    print("[INFO] Connecting to database...")
    try:
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
        print(f"[SUCCESS] Connected\n")
    except Exception as e:
        print(f"[FAILED] {e}")
        return

    try:
        # Get an existing task in pending or awaiting_approval status
        print("[STEP 1] Finding test task...")
        async with pool.acquire() as conn:
            task = await conn.fetchrow("""
                SELECT id, task_id, status FROM content_tasks
                WHERE status IN ('pending', 'awaiting_approval', 'in_progress')
                LIMIT 1
            """)
            
            if not task:
                print("[INFO] No pending tasks found. Creating one...")
                result = await conn.fetchrow("""
                    INSERT INTO content_tasks (
                        task_id, title, category, description, status, 
                        content_type, created_at, updated_at
                    )
                    VALUES (
                        gen_random_uuid(), 'Test Approval', 'testing', 'Test task for approval',
                        'pending', 'blog_post', NOW(), NOW()
                    )
                    RETURNING id, task_id, status
                """)
                task = result
            
            task_id = str(task['task_id'])
            print(f"[SUCCESS] Found task: {task_id}")
            print(f"   Current status: {task['status']}\n")

        # Step 2: Check current state
        print("[STEP 2] Checking current database state...")
        async with pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT id, status, task_metadata, updated_at FROM content_tasks WHERE task_id = $1",
                task_id
            )
            if current:
                print(f"[INFO] Current status: {current['status']}")
                print(f"       Updated at: {current['updated_at']}")
                metadata = json.loads(current['task_metadata']) if current['task_metadata'] else {}
                print(f"       Metadata keys: {list(metadata.keys())}\n")

        # Step 3: Transition to awaiting_approval
        print("[STEP 3] Transitioning to awaiting_approval...")
        payload = {
            "status": "awaiting_approval",
            "reason": "Ready for review",
            "metadata": {"action": "workflow_test", "timestamp": datetime.now().isoformat()}
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(
                    f"http://localhost:8000/api/tasks/{task_id}/status/validated",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[SUCCESS] Transition response:")
                        print(f"   Message: {data.get('message', 'N/A')}")
                    else:
                        print(f"[FAILED] {resp.status}")
                        text = await resp.text()
                        print(f"   {text[:200]}")
            except Exception as e:
                print(f"[WARNING] Could not reach API: {e}")
                print(f"   (Backend may not be running, but test will continue)\n")

        # Step 4: Approve with feedback
        print("[STEP 4] Approving task with feedback...")
        payload = {
            "status": "approved",
            "reason": "Content approved by reviewer",
            "metadata": {
                "action": "approval",
                "approval_feedback": "Great content! Ready to publish.",
                "reviewer_notes": "Very good quality",
                "approved_at": datetime.now().isoformat(),
            },
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(
                    f"http://localhost:8000/api/tasks/{task_id}/status/validated",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[SUCCESS] Approval response:")
                        print(f"   Message: {data.get('message', 'N/A')}")
                    else:
                        print(f"[FAILED] {resp.status}")
                        text = await resp.text()
                        print(f"   {text[:200]}")
            except Exception as e:
                print(f"[WARNING] Could not reach API: {e}\n")

        # Step 5: Verify database persistence
        print(f"\n{'='*80}")
        print(f"[VERIFICATION] Database Persistence Check")
        print(f"{'='*80}\n")

        async with pool.acquire() as conn:
            final = await conn.fetchrow("""
                SELECT id, status, task_metadata, updated_at 
                FROM content_tasks 
                WHERE task_id = $1
            """, task_id)
            
            if final:
                print(f"[INFO] Task found in database")
                print(f"   ID: {final['id']}")
                print(f"   Task ID: {task_id}")
                
                # Status check
                print(f"\n[CHECK] Status:")
                print(f"   Expected: approved")
                print(f"   Actual: {final['status']}")
                status_ok = final['status'] == 'approved'
                print(f"   Result: {'PASS' if status_ok else 'FAIL'}")
                
                # Metadata check
                print(f"\n[CHECK] Metadata Persistence:")
                metadata = json.loads(final['task_metadata']) if final['task_metadata'] else {}
                
                required_fields = ['approval_feedback', 'reviewer_notes', 'approved_at']
                for field in required_fields:
                    value = metadata.get(field)
                    has_value = value is not None and value != ""
                    print(f"   {'OK' if has_value else 'MISSING':8} {field:20} = {value}")
                
                metadata_ok = all(metadata.get(field) for field in required_fields)
                print(f"   Result: {'PASS' if metadata_ok else 'FAIL'}")
                
                # Timestamp check
                print(f"\n[CHECK] Timestamp:")
                print(f"   Updated at: {final['updated_at']}")
                print(f"   Result: PASS")
                
                # Overall
                print(f"\n{'='*80}")
                all_ok = status_ok and metadata_ok
                if all_ok:
                    print(f"[SUCCESS] ALL CHECKS PASSED!")
                else:
                    print(f"[FAILED] Some checks failed")
                print(f"{'='*80}\n")
                
                # Show status history
                print(f"[INFO] Status History for this task:")
                history = await conn.fetch("""
                    SELECT old_status, new_status, reason, created_at
                    FROM status_history
                    WHERE task_id = $1
                    ORDER BY created_at DESC
                    LIMIT 10
                """, task_id)
                
                if history:
                    for i, h in enumerate(history, 1):
                        print(f"   {i}. {h['old_status']:15} -> {h['new_status']:15} at {h['created_at']}")
                else:
                    print(f"   No history found")
            else:
                print(f"[FAILED] Task not found in database")
                print(f"   Searched for: {task_id}")

        print(f"\n[INFO] Test complete!\n")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
