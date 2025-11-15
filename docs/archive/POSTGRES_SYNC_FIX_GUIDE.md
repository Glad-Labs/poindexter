# 🔍 PostgreSQL Sync Issue - Complete Analysis & Fix Guide

**Last Updated:** November 2025  
**Issue:** PostgreSQL database not syncing with frontend; all tasks showing same topic  
**Root Cause:** Tasks stuck in 'pending' status, never reaching 'completed' state for publishing

---

## 📊 The Problem Explained

### What You Reported:

- PostgreSQL database not syncing with frontend
- All tasks showing the same topic
- Post generation pipeline not working

### What I Found:

Your pipeline has a **critical bottleneck at task completion**:

```
✅ oversight-hub creates task (status='pending')
  ↓
✅ Task stored in PostgreSQL
  ↓
❌ **BLOCKED: Task never completes (stays 'pending' forever)**
  ↓
❌ Cannot publish (requires status='completed')
  ↓
❌ Post never created in Strapi
  ↓
❌ Nothing displays on public-site
```

---

## 🔴 Root Causes Identified

### Issue #1: Task Execution Blocked

- **Location:** `src/cofounder_agent/services/task_executor.py`
- **Problem:** Tasks created with `status='pending'` never change to `status='completed'`
- **Why:** Content generation fails or task executor not processing the task
- **Impact:** Publishing endpoint returns 409 Conflict error (requires 'completed' status)

### Issue #2: No Auto-Publishing

- **Location:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx`
- **Problem:** Publishing requires manual click after task completion
- **Why:** No automatic publish trigger when task completes
- **Impact:** Even if tasks completed, posts wouldn't publish automatically

### Issue #3: "All Same Topic" Bug

- **Location:** Unknown (investigate with diagnosis steps below)
- **Problem:** All tasks/posts display with identical topic
- **Theory 1:** Topic field not being captured in form
- **Theory 2:** Database stores wrong field
- **Theory 3:** Frontend display logic using wrong field

---

## 🔧 How to Diagnose

### Step 1: Query PostgreSQL (VS Code Extension)

1. **Open PostgreSQL extension** in VS Code left sidebar
2. **Right-click your connection** → "New Query"
3. **Copy/paste this SQL** to understand the data:

```sql
-- See task statistics
SELECT COUNT(*) as total_tasks FROM tasks;

-- See status breakdown
SELECT status, COUNT(*) as count FROM tasks GROUP BY status;

-- See recent tasks
SELECT id, title, topic, status, created_at
FROM tasks
ORDER BY created_at DESC
LIMIT 10;

-- See if topics are unique or duplicated
SELECT DISTINCT topic FROM tasks LIMIT 10;

-- See if any posts exist in Strapi
SELECT COUNT(*) as posts_count FROM posts;
```

**What you'll likely see:**

- ❌ All tasks have `status = 'pending'`
- ❌ Zero tasks with `status = 'completed'`
- ❌ Zero posts in Strapi (since nothing published)
- ✓ Topics might be unique OR all same (tells us where bug is)

### Step 2: Check FastAPI Logs

In your FastAPI terminal window, look for:

- ✅ `Background task executor started successfully` (should be present)
- ✅ `🔗 Pipeline: Orchestrator→Critique→Strapi` (should be present)
- ❌ Any error messages about:
  - Model router / LLM connection
  - Orchestrator failures
  - Database connection issues

### Step 3: Test Publishing Manually

Prove that the publish endpoint WORKS by manually completing a task:

```sql
-- Find a pending task
SELECT id, title FROM tasks WHERE status='pending' LIMIT 1;

-- Copy the ID, then complete it manually
UPDATE tasks
SET status='completed',
    result='{"title":"Test Post","content":"This is test content"}'
WHERE id='<PASTE_ID_HERE>';
```

Then test the publish endpoint in PowerShell:

```powershell
$taskId = "your-task-id-from-above"
Invoke-WebRequest -Uri "http://localhost:8000/api/tasks/$taskId/publish" -Method POST
```

**If this works:** The issue is that tasks never complete (Issue #1)  
**If this fails:** The issue is in the publish endpoint itself (Issue #2)

### Step 4: Run Diagnostic Script

```powershell
# From project root:
.\scripts\test-pipeline.ps1
```

This script will:

- ✅ Check if backend is running
- ✅ Fetch task list from database
- ✅ Show task status breakdown
- ✅ Test publish endpoint
- ✅ Check if posts exist in Strapi
- ✅ Give you a diagnosis

---

## 🛠️ How to Fix

### Fix #1: Enable Task Execution (CRITICAL)

**The task executor IS configured** to run in `src/cofounder_agent/main.py` (line 234-241).

However, tasks might not be completing. Check:

1. **Is Ollama running?** (if using local models)

   ```powershell
   ollama serve  # Run this in a separate terminal
   ```

2. **Check for LLM errors** in FastAPI logs
   - Look for messages about model routing failures
   - Look for API key validation errors

3. **Manual test:** Try to trigger content generation

   ```powershell
   # Create a task via oversight-hub
   # Wait 30 seconds
   # Check if status changed to 'completed' in database

   # Query: SELECT status FROM tasks ORDER BY created_at DESC LIMIT 1;
   ```

### Fix #2: Add Auto-Publishing (NICE TO HAVE)

Once tasks complete, automatically publish them:

**In `src/cofounder_agent/services/task_executor.py`:**

Add after line 195 (where status is updated to 'completed'):

```python
# Auto-publish completed tasks
logger.info(f"📤 Auto-publishing task {task_id}...")
try:
    # Call publish_task endpoint
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://localhost:8000/api/tasks/{task_id}/publish",
            json={}
        )
        if response.status_code == 200:
            logger.info(f"✅ Task published automatically")
        else:
            logger.error(f"❌ Auto-publish failed: {response.status_code}")
except Exception as e:
    logger.error(f"❌ Auto-publish error: {e}")
```

### Fix #3: Debug "Same Topic" Issue

**Once tasks are completing and publishing:**

1. **Create new task in oversight-hub** with unique topic: "AI in Healthcare 2025"
2. **Check database:**
   ```sql
   SELECT topic FROM tasks WHERE created_at > now() - interval '1 minute';
   ```
3. **If database shows different topics** but oversight-hub shows same:
   - 🐛 UI display bug in oversight-hub
   - Check: `web/oversight-hub/src/components/tasks/TaskManagement.jsx`

4. **If database shows same topic:**
   - 🐛 Form input bug in oversight-hub or database storage bug
   - Check: `web/oversight-hub/src/components/NewTaskModal.jsx`

---

## 📋 Diagnostic Files You Now Have

I've created two diagnostic scripts:

### `scripts/DIAGNOSE_PIPELINE.sql`

- Complete SQL diagnostic script
- Run in PostgreSQL VS Code extension
- Shows task statistics, schema, data flow
- Includes interpretation guide

**How to use:**

```
1. Open PostgreSQL extension in VS Code
2. Right-click connection → New Query
3. Copy/paste content from DIAGNOSE_PIPELINE.sql
4. Run each query section to understand your data
```

### `scripts/test-pipeline.ps1`

- PowerShell test script
- Tests complete pipeline: tasks → Strapi → posts
- Gives you a diagnosis with next steps

**How to use:**

```powershell
.\scripts\test-pipeline.ps1
```

---

## 🎯 What to Do Now (Action Items)

### Immediate (5 minutes)

- [ ] Run diagnostic queries in PostgreSQL to understand current state
- [ ] Check FastAPI logs for errors during startup
- [ ] Run `test-pipeline.ps1` for automatic diagnosis

### Short Term (15 minutes)

- [ ] Verify Ollama is running if using local models
- [ ] Check for LLM connection errors in logs
- [ ] Manually complete one task and test publish endpoint

### Medium Term (30 minutes)

- [ ] Implement auto-publishing if tasks are completing
- [ ] Debug "same topic" issue with database vs UI comparison
- [ ] Verify end-to-end pipeline with new task creation

### Long Term (Optional)

- [ ] Add task execution status monitoring to oversight-hub
- [ ] Add error handling for task execution failures
- [ ] Implement task retry logic for failed generations

---

## 📊 Data Flow (What Should Happen)

```
1. oversight-hub (NewTaskModal.jsx)
   ↓ POST /api/tasks with topic, title, description
   ↓
2. FastAPI (task_routes.py - create_task)
   ↓ Creates task in PostgreSQL with status='pending'
   ↓
3. PostgreSQL (tasks table)
   ↓ Task stored with pending status
   ↓
4. Task Executor (task_executor.py) [Background Service]
   ↓ Polls for pending tasks
   ↓ Calls orchestrator to generate content
   ↓ Updates task with result (content generated)
   ↓ Updates status to 'completed'
   ↓
5. oversight-hub (TaskManagement.jsx)
   ↓ User clicks "Approve/Publish"
   ↓ POST /api/tasks/{id}/publish
   ↓
6. FastAPI (task_routes.py - publish_task)
   ↓ Verifies status='completed'
   ↓ Calls StrapiPublisher.create_post()
   ↓
7. StrapiPublisher (content_publisher.py)
   ↓ Connects to PostgreSQL
   ↓ Inserts post into posts table
   ↓ Returns post_id
   ↓
8. Strapi CMS (posts table)
   ↓ Post stored with title, content, slug
   ↓
9. public-site (api.js)
   ↓ getPaginatedPosts() fetches from /api/posts
   ↓ Strapi returns posts with correct data
   ↓
10. public-site Display
    ✅ Post displays with correct topic/title
```

**Current Status:** Blocked at step 4 (Task execution)

---

## 🔗 Related Files

| File                                                        | Purpose                 | Status         |
| ----------------------------------------------------------- | ----------------------- | -------------- |
| `src/cofounder_agent/main.py`                               | Starts task executor    | ✅ Configured  |
| `src/cofounder_agent/services/task_executor.py`             | Executes pending tasks  | ⚠️ Check logs  |
| `src/cofounder_agent/routes/task_routes.py`                 | Task CRUD & publish     | ✅ Implemented |
| `src/cofounder_agent/services/content_publisher.py`          | Publishes to Strapi     | ✅ Implemented |
| `web/oversight-hub/src/components/NewTaskModal.jsx`         | Create task form        | ✅ Working     |
| `web/oversight-hub/src/components/tasks/TaskManagement.jsx` | Approve/publish UI      | ✅ Working     |
| `web/public-site/lib/api.js`                                | Fetch posts from Strapi | ✅ Correct     |

---

## 📞 Next Steps

1. **Run diagnostics** using the scripts above
2. **Report findings** - what does the database show?
3. **I'll provide specific fixes** based on your diagnosis results
4. **Together we'll verify** the end-to-end pipeline works

The good news: **Your architecture is correct!** The pipeline just needs the task execution step to complete. Once that's fixed, everything else will work automatically.

---

**Questions?** Check the diagnostic scripts and FastAPI logs - they'll show you exactly what's happening in your system.
