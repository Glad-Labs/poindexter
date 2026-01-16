# Bug Fix: Duplicate Task ID Insertion Error

**Date:** January 15, 2026  
**Issue:** `asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "content_tasks_task_id_key"`  
**Severity:** Critical (prevents task creation)  
**Status:** ✅ FIXED

---

## Problem Description

When creating a blog post task via POST `/api/tasks`, the endpoint returns 201 Created successfully, but then crashes in the background task processor with a duplicate key constraint violation on `content_tasks.task_id`.

### Error Logs

```
INFO:     127.0.0.1:55589 - "POST /api/tasks HTTP/1.1" 201 Created
ERROR:services.tasks_db:❌ Failed to add task: duplicate key value violates unique constraint "content_tasks_task_id_key"
DETAIL:  Key (task_id)=(36f437d5-7b3f-4d7a-aa55-aef0e42b355f) already exists.
```

---

## Root Cause

The task was being inserted into the database **twice**:

### Execution Flow (Before Fix)

```
1. POST /api/tasks
   ├─ _handle_blog_post_creation()
   │  ├─ Generate task_id (UUID)
   │  ├─ db_service.add_task(task_data)  ← INSERT #1 ✓
   │  └─ asyncio.create_task(_run_blog_generation())
   │     └─ process_content_generation_task()
   │        ├─ STAGE 1: db_service.add_task()  ← INSERT #2 ✗ DUPLICATE!
   │        └─ [Background task continues...]
   └─ Return 201 Created
```

### Why This Happens

The task_routes.py handler creates and inserts the task into the database, then launches a background task via `asyncio.create_task()`. The background task processor (`content_router_service.py`) was attempting to insert the same task again in STAGE 1, not realizing it had already been persisted.

---

## Solution

**Changed:** `content_router_service.py` STAGE 1  
**From:** `await database_service.add_task()` (insert)  
**To:** `await database_service.get_task()` (verify exists)

### Execution Flow (After Fix)

```
1. POST /api/tasks
   ├─ _handle_blog_post_creation()
   │  ├─ Generate task_id (UUID)
   │  ├─ db_service.add_task(task_data)  ← INSERT ✓
   │  └─ asyncio.create_task(_run_blog_generation())
   │     └─ process_content_generation_task()
   │        ├─ STAGE 1: db_service.get_task()  ← VERIFY ✓ (no insert)
   │        ├─ STAGE 2: Generate blog content
   │        ├─ STAGE 3: Critique content
   │        └─ [Continue pipeline...]
   └─ Return 201 Created
```

### Code Changes

**File:** `src/cofounder_agent/services/content_router_service.py`  
**Lines:** 375-395

**Before:**

```python
# ================================================================================
# STAGE 1: CREATE CONTENT_TASK RECORD
# ================================================================================
logger.info("📋 STAGE 1: Creating content_task record...")

# Use consolidated add_task() method
logger.debug(f"[BG-TASK] Calling database_service.add_task()...")
task_id_created = await database_service.add_task(
    {
        "task_id": task_id,
        "id": task_id,
        "request_type": "api_request",
        "task_type": "blog_post",
        "status": "pending",
        "topic": topic,
        "style": style,
        "tone": tone,
        "target_length": target_length,
        "approval_status": "pending",
    }
)

result["content_task_id"] = task_id_created
result["stages"]["1_content_task_created"] = True
logger.info(f"✅ Content task created: {task_id_created}\n")
```

**After:**

```python
# ================================================================================
# STAGE 1: VERIFY TASK RECORD EXISTS
# ================================================================================
logger.info("📋 STAGE 1: Verifying task record exists...")

# Task already created by task_routes.py before background task launched
# Just verify it exists in database
logger.debug(f"[BG-TASK] Verifying task {task_id} exists in database...")
try:
    existing_task = await database_service.get_task(task_id)
    if existing_task:
        logger.info(f"✅ Task verified in database: {task_id}\n")
        result["content_task_id"] = task_id
        result["stages"]["1_content_task_created"] = True
    else:
        logger.warning(f"⚠️  Task {task_id} not found - this should not happen")
        result["stages"]["1_content_task_created"] = False
except Exception as e:
    logger.error(f"❌ Failed to verify task: {e}")
    result["stages"]["1_content_task_created"] = False
```

---

## Testing the Fix

### 1. Clear any stuck tasks (optional)

```sql
DELETE FROM content_tasks WHERE task_id = '36f437d5-7b3f-4d7a-aa55-aef0e42b355f';
```

### 2. Create a new blog post task

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{
    "task_type": "blog_post",
    "topic": "AI in Healthcare",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000
  }'
```

### 3. Expected Response

```json
{
  "id": "36f437d5-7b3f-4d7a-aa55-aef0e42b355f",
  "task_type": "blog_post",
  "status": "pending",
  "created_at": "2026-01-15T10:30:00.000Z",
  "message": "Blog post task created and queued"
}
```

### 4. Monitor logs

```bash
# Should see:
# INFO: 127.0.0.1:... - "POST /api/tasks HTTP/1.1" 201 Created
# INFO: ✅ [BLOG_TASK] Created: 36f437d5-7b3f-4d7a-aa55-aef0e42b355f
# INFO: 📋 STAGE 1: Verifying task record exists...
# INFO: ✅ Task verified in database: 36f437d5-7b3f-4d7a-aa55-aef0e42b355f
# INFO: ✍️  STAGE 2: Generating blog content...
# (no duplicate key error!)
```

---

## Impact Analysis

### What Changed

- **Background task now verifies instead of re-inserting** task record
- Database constraint violation eliminated
- Task creation pipeline completes successfully

### What Stayed the Same

- Task creation API (POST /api/tasks) - same input/output
- Content generation pipeline - all 6 stages still execute
- Database schema - no changes needed
- Other task types (social_media, email, newsletter, etc.) - unaffected

### Affected Flows

- ✅ Blog post creation - NOW WORKS
- ✅ Background task processing - NOW WORKS
- ✅ Content generation pipeline - NOW COMPLETES

---

## Prevention

To prevent similar issues in the future:

1. **Separate concerns:** Route handlers should create tasks, not background processors
2. **Idempotency:** Background tasks should verify existence, not recreate
3. **Task lifecycle:** Clear ownership - route handler owns creation, background task owns processing
4. **Testing:** Add integration tests for task creation + background processing flow

---

## Deployment Notes

- **No database migration required** - just code change
- **No data cleanup required** - fix prevents future duplicates
- **Existing stuck tasks:** May need manual cleanup with the SQL query above
- **Rolling deployment:** Safe to deploy while tasks are running

---

## Related Files

- [task_routes.py](src/cofounder_agent/routes/task_routes.py) - Route handler (creates task, no changes)
- [content_router_service.py](src/cofounder_agent/services/content_router_service.py) - Background processor (STAGE 1 fixed)
- [tasks_db.py](src/cofounder_agent/services/tasks_db.py) - Database layer (unchanged)
- [database_service.py](src/cofounder_agent/services/database_service.py) - Service coordinator (unchanged)
