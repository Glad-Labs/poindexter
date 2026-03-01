#!/usr/bin/env python3
"""Debug script to test if update_task_status() actually persists database changes."""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env.local first
env_path = Path(".env.local")
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)

# Add the backend path
sys.path.insert(0, "src/cofounder_agent")

from services.database_service import DatabaseService
from services.tasks_db import TasksDatabase
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator


async def main():
    print("\n" + "=" * 80)
    print("DEBUGGING: Update Task Status Persistence")
    print("=" * 80 + "\n")

    # Initialize database service
    try:
        db_service = DatabaseService()
        print("[OK] Database service initialized")
    except Exception as e:
        print(f"[ERROR] Failed to initialize database: {e}")
        return

    # Get an existing task in awaiting_approval state
    print("\n[STEP 1] Finding a task in awaiting_approval state...")
    try:
        tasks, total = await db_service.get_tasks_paginated(
            status="awaiting_approval",
            limit=1
        )
        if not tasks:
            print("[ERROR] No tasks found in awaiting_approval state")
            return

        task = tasks[0]
        task_id = task.get("task_id") or task.get("id")
        print(f"[OK] Found task: {task_id}")
        print(f"     Current status: {task.get('status')}")
        print(f"     Task type: {task.get('task_type')}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch tasks: {e}")
        import traceback
        traceback.print_exc()
        return

    # Test 2: Call update_task_status()
    print("\n[STEP 2] Calling update_task_status to 'approved'...")
    try:
        result = await db_service.update_task_status(
            task_id,
            "approved",
            result=json.dumps({"test": "approval_debug", "timestamp": datetime.now(timezone.utc).isoformat()})
        )
        if result:
            print(f"[OK] update_task_status() returned a result")
            print(f"     Returned status: {result.get('status')}")
            print(f"     Return type: {type(result)}")
        else:
            print(f"[WARNING] update_task_status() returned None")
    except Exception as e:
        print(f"[ERROR] update_task_status() raised exception: {e}")
        import traceback
        traceback.print_exc()
        return

    # Test 3: Re-fetch task from database
    print("\n[STEP 3] Re-fetching task to verify update persisted...")
    try:
        refetched_task = await db_service.get_task(task_id)
        if refetched_task:
            refetched_status = refetched_task.get("status")
            print(f"[OK] Task fetched successfully")
            print(f"     Status in DB: {refetched_status}")

            if refetched_status == "approved":
                print(f"\n[SUCCESS] Status was actually updated in database!")
            else:
                print(f"\n[FAILURE] Status is still '{refetched_status}', not 'approved'")
                print(f"     This means update_task_status() is NOT persisting to DB")
        else:
            print(f"[ERROR] Task not found after update")
    except Exception as e:
        print(f"[ERROR] Failed to re-fetch task: {e}")
        import traceback
        traceback.print_exc()
        return

    # Test 4: Check what the update SQL looks like
    print("\n[STEP 4] Analyzing the SQL that gets generated...")
    builder = ParameterizedQueryBuilder()
    updates = {
        "status": "test_status",
        "updated_at": datetime.now(timezone.utc),
    }

    where_column = "id" if str(task_id).isdigit() else "task_id"
    where_value = int(task_id) if str(task_id).isdigit() else str(task_id)

    sql, params = builder.update(
        table="content_tasks",
        updates=updates,
        where_clauses=[(where_column, SQLOperator.EQ, where_value)],
        return_columns=["*"]
    )

    print(f"[OK] Generated SQL:")
    print(f"     {sql}")
    print(f"     Where column: {where_column}")
    print(f"     Where value: {where_value}")
    print(f"     Num params: {len(params)}")

    print("\n" + "=" * 80)
    print("DEBUG SESSION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
