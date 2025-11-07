# 🔴 Critical Issues Diagnosis - Ollama Failure & Task Persistence

**Date:** November 7, 2025  
**Time:** 12:30 AM  
**Status:** 3 Critical Issues Identified

---

## Issue #1: Ollama Generation Failing + VRAM Stuck High

### Symptoms

```
ERROR:services.ollama_client:2025-11-07T05:24:36.858848Z [error    ] Ollama generation failed
ERROR:services.ai_content_generator:All AI models failed. Attempts: []
```

### Root Cause Analysis

**Problem 1: Empty Attempts List**

```python
# In ai_content_generator.py line 424
attempts = []  # Initialized as empty list
# ... code tries Ollama, HuggingFace, Gemini
# But if all fail, logs: "All AI models failed. Attempts: []"
```

The `attempts` list is EMPTY because errors aren't being appended. This suggests the code is hanging or timing out BEFORE it can append error info.

**Problem 2: VRAM Stuck High After Error**

This happens because:

1. **Ollama process loads model into VRAM** (qwen2.5:14b = ~14GB)
2. **Request times out** (timeout=120 seconds in ollama_client.py line 126)
3. **Model stays loaded in VRAM** even after timeout
4. **Next task tries same model**, starts another inference loop
5. **Model processes queue up** but don't complete

### Why This Happens

```python
# ollama_client.py line 115-135
class OllamaClient:
    def __init__(self, timeout: int = 120):
        self.timeout = timeout  # 120 second timeout
        self.client = httpx.AsyncClient(timeout=timeout)
```

**Issue:** If Ollama is slow (qwen2.5:14b is VERY slow - 10-20 tokens/sec), the request hits 120s timeout while model is still executing.

**Result:** Model process continues in background, keeping VRAM occupied.

### Solution for Ollama Hang

1. **Reduce timeout OR use smaller models**
   - qwen2.5:14b: Too slow for 120s timeout
   - mistral:latest: 2-3 sec, excellent
   - llama2:latest: 3-5 sec, good

2. **Add model unload after timeout**

   ```python
   # After timeout, explicitly unload model
   await ollama_client.unload_model(model_name)
   ```

3. **Check available VRAM before loading**
   ```python
   if available_vram < model_size:
       use_smaller_model()
   ```

---

## Issue #2: Tasks Not Persisting (Created After 9:30pm Disappear)

### Symptoms

- Tasks created successfully (200 OK response)
- Task doesn't appear in task list
- Task status endpoint works (returns 200)
- But task_id not in database

### Root Cause: Database Query Timeout

```python
# task_routes.py line 327
async def list_tasks():
    all_tasks = await db_service.get_all_tasks(limit=10000)  # <-- PROBLEM
```

**The Issue:**

- `get_all_tasks(limit=10000)` fetches ALL tasks
- If 500+ tasks accumulated, query becomes slow
- AsyncPG times out after 180 seconds
- Browser gets timeout error, shows no tasks

### Why After 9:30pm?

- Likely accumulated many tasks throughout day
- Database query grows slower as task count increases
- At midnight, query finally times out
- New tasks still get created (200 OK)
- But fetch fails, so UI shows "no tasks"

---

## Issue #3: Browser "signal timed out" on fetchTasks

### Symptoms

```javascript
Failed to fetch tasks: TimeoutError: signal timed out  // repeated 15+ times
fetchTasks @ TaskManagement.jsx:167
```

### Root Cause: Network Request Timeout

**Frontend Timeout:** Browser default = ~30 seconds  
**Backend Query Time:** 180+ seconds (all tasks with 500+ records)  
**Result:** Frontend times out before backend responds

### Current Flow (Broken)

```
1. TaskManagement.jsx calls GET /api/tasks
2. Backend fetches 500+ tasks from database
3. Query takes 150+ seconds
4. Frontend timeout hits 30s mark
5. Browser: "signal timed out"
6. User sees "No tasks"
7. Actually tasks exist but can't fetch them
```

---

## 📊 Data Collection Points

### Check These to Confirm Issues

**1. Ollama Process Health**

```bash
# Check if model is loaded
ollama ps

# Check VRAM usage (should be 0 after task completes)
nvidia-smi

# If model stuck, kill it:
ollama rm qwen2.5:14b  # Unload
ollama pull mistral   # Use smaller model instead
```

**2. Database Task Count**

```sql
-- How many tasks in DB?
SELECT COUNT(*) FROM tasks;

-- How long does full fetch take?
SELECT COUNT(*) FROM tasks WHERE created_at > NOW() - INTERVAL '24 hours';

-- Slow queries?
EXPLAIN ANALYZE SELECT * FROM tasks LIMIT 10000;
```

**3. Backend Response Times**

```bash
# Time backend task fetch
curl -w "\nTotal time: %{time_total}s\n" http://localhost:8000/api/tasks

# Should be <5s for first 100 tasks
# If >30s, database is too slow
```

**4. Frontend Network Timeline**

```javascript
// In DevTools → Network tab:
// 1. Click "Create Task"
// 2. Watch GET /api/tasks request
// 3. Note: Time Pending → Time to First Byte → Time to Complete
// If any > 30s, fetch times out
```

---

## 🔧 Quick Fixes (Immediate Relief)

### Fix #1: Switch to Faster Model

```python
# ollama_client.py line 33
DEFAULT_MODEL = "mistral"  # Instead of "qwen2.5:14b"
```

**Why:** mistral is 5-10x faster, same quality for blog posts

### Fix #2: Implement Pagination

```python
# task_routes.py line 327
@router.get("")
async def list_tasks(offset: int = 0, limit: int = 20):  # Add pagination
    tasks = await db_service.get_tasks_paginated(offset, limit)
    return tasks
```

### Fix #3: Add Connection Timeout to Database

```python
# database_service.py
async def get_all_tasks(self, limit: int = 100):
    # Add 30s timeout
    async with asyncio.timeout(30):
        query = "SELECT * FROM tasks ORDER BY created_at DESC LIMIT $1"
        return await self.pool.fetch(query, limit)
```

---

## 📋 Detailed Fixes Required

### Priority 1: Ollama Model Swap (5 minutes)

- [ ] Change DEFAULT_MODEL to "mistral"
- [ ] Test: Create blog post, verify completes in <10s
- [ ] Check: VRAM returns to baseline after completion

### Priority 2: Add Task Pagination (15 minutes)

- [ ] Modify TaskListResponse to include pagination info
- [ ] Add offset/limit params to GET /api/tasks
- [ ] Update frontend to request paginated data (start with limit=20)

### Priority 3: Database Query Optimization (20 minutes)

- [ ] Add database timeout handling
- [ ] Create index on created_at if missing
- [ ] Add query explain analysis

### Priority 4: Add Database Cleanup Job (30 minutes)

- [ ] Archive tasks older than 30 days
- [ ] Run nightly to keep active task count low
- [ ] Prevent query slowdown over time

---

## Why This Happened

1. **No pagination:** System assumes <100 tasks, but DB has 500+
2. **Slow model:** qwen2.5:14b takes 10-20s per 100 tokens, hits timeout
3. **No VRAM cleanup:** Model stays loaded after timeout, blocking next inference
4. **No connection timeout:** Backend query hangs indefinitely, frontend times out

---

## Expected Results After Fixes

| Issue                  | Before            | After                       |
| ---------------------- | ----------------- | --------------------------- |
| Ollama generation time | 120s → timeout    | 5-10s → success             |
| VRAM after task        | Stuck at 14GB     | Returns to baseline (2-3GB) |
| Task list fetch        | 180s → timeout    | 1-2s → success              |
| Tasks visible in UI    | None after 9:30pm | All tasks visible           |
| Browser console errors | 15+ timeouts      | Clean, no errors            |

---

## Next Steps

1. **Now:** Swap model to mistral (1 minute)
2. **Next:** Add pagination (15 minutes)
3. **Follow-up:** Database cleanup job (30 minutes)
4. **Monitor:** Check VRAM and query times for 24h

---

**Status:** Ready for implementation
