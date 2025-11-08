# ✅ Task Pipeline Fix - Verification Checklist

**Date:** November 7, 2025  
**Status:** Fix Applied and Ready for Testing

---

## 📋 Pre-Testing Verification

### Code Changes Applied

- [x] File: `web/oversight-hub/src/services/taskService.js`
  - Status: ✅ REPLACED (Firebase → API)
  - Verified: Calls `${API_BASE_URL}/api/tasks` instead of Firebase
  - Lines: 131 total (was ~100, now cleaner)
  - Functions: getTasks, createTask, updateTask all updated

### Backend Status

- [x] File: `src/cofounder_agent/routes/task_routes.py`
  - Status: ✅ VERIFIED CORRECT (uses PostgreSQL)
  - No changes needed
- [x] File: `src/cofounder_agent/services/database_service.py`
  - Status: ✅ VERIFIED CORRECT (asyncpg to PostgreSQL)
  - No changes needed

### Documentation Created

- [x] **QUICK_START_TASK_PIPELINE.md** - 3-step quick start guide
- [x] **TASK_PIPELINE_COMPLETE_FIX.md** - Full technical documentation
- [x] **scripts/Test-TaskPipeline.ps1** - Automated verification script

---

## 🧪 Testing Procedure

### Phase 1: Service Startup (2 minutes)

**Command:**

```bash
npm run dev
# Or individually:
cd web/oversight-hub && npm start
cd src/cofounder_agent && python -m uvicorn cofounder_agent.main:app --reload
```

**Verification:**

- [ ] Oversight Hub loads at http://localhost:3001 (or 3002 if 3001 taken)
- [ ] Backend ready at http://localhost:8000
- [ ] Backend returns 200 on http://localhost:8000/api/health
- [ ] No errors in either terminal

**Expected Errors (ignore these):**

- `⚠ Unable to find font-awesome fonts` - OK, CSS only
- `⚠ Failed to compile` - Only if you made edits

---

### Phase 2: Browser Prep (1 minute)

**Steps:**

1. Open DevTools: Press `F12`
2. Go to Application tab
3. Click "Clear site data" button
4. Refresh page: `Ctrl+Shift+R` (hard refresh)

**Verification:**

- [ ] Oversight Hub loads cleanly
- [ ] No errors in console
- [ ] Ready to create tasks

---

### Phase 3: Task Creation Test (3 minutes)

**Manual Test:**

1. Navigate to task creation area (likely TaskManagement page)
2. Fill in:
   - Task Name: "Pipeline Verification Test"
   - Topic: "Testing" (or any value)
   - Keywords: "test, pipeline" (or any value)
3. Click: "Create Task" button

**Verification:**

- [ ] No error popups
- [ ] Task shows success message (green checkmark or similar)
- [ ] Console shows: `POST http://localhost:8000/api/tasks 201 Created`
- [ ] NO console shows: "TimeoutError: signal timed out"

---

### Phase 4: Task List Display (2 minutes)

**Automatic (if TaskManagement has auto-refresh):**

- [ ] Task appears in list within 5 seconds
- [ ] Console shows: `GET http://localhost:8000/api/tasks 200 OK`

**Manual (if needed):**

1. Refresh page: `Ctrl+R`
2. Wait 2-3 seconds for list to populate
3. Look for your task

**Verification:**

- [ ] Your task appears in the list
- [ ] Task status shows "pending"
- [ ] No errors in console
- [ ] No timeout messages

---

### Phase 5: Database Verification (2 minutes)

**Command in PowerShell:**

```powershell
# Verify environment variable is set
echo $env:DATABASE_URL

# Count total tasks
psql $env:DATABASE_URL -c "SELECT COUNT(*) as total_tasks FROM tasks;"

# See your recent tasks
psql $env:DATABASE_URL -c "SELECT id, task_name, status, created_at FROM tasks ORDER BY created_at DESC LIMIT 5;"
```

**Expected Output:**

```
 total_tasks
─────────────
    1         ← Your test task

          id          │    task_name     │ status │        created_at
─────────────────────┼──────────────────┼────────┼─────────────────────
 550e8400-e29b...   │ Pipeline Verif... │ pending │ 2025-11-07 14:30:21
```

**Verification:**

- [ ] Can connect to PostgreSQL (no "FATAL" errors)
- [ ] Tasks table has entries
- [ ] Your test task is in the database
- [ ] Created timestamp is recent (within last 5 minutes)

---

## 🔍 Console Output Verification

### ✅ Expected Console Messages (POST - Task Creation)

```
POST http://localhost:8000/api/tasks 201 Created
GET http://localhost:8000/api/tasks?offset=0&limit=20 200 OK
```

### ❌ If You See These (Old Bug Still Present)

```
Failed to fetch tasks: TypeError: Cannot read properties of undefined (reading 'getDocs')
Failed to fetch tasks: TimeoutError: signal timed out
Failed to fetch from undefined
```

**Action:**

- Check taskService.js was replaced (see below)
- Clear browser cache again
- Restart Oversight Hub

### ❌ If You See These (New Issues)

```
POST http://localhost:8000/api/tasks 404 Not Found
GET http://localhost:8000/api/tasks 401 Unauthorized
```

**Action:**

- 404: Backend not running (start it)
- 401: JWT token issue (reload page, login)

---

## 🔧 Verify Code Changes

### Check taskService.js Was Updated

**In terminal:**

```powershell
# Look for API calls (should be there)
Select-String -Path "web/oversight-hub/src/services/taskService.js" -Pattern "api/tasks"

# Should return:
#   response = await fetch(`${API_BASE_URL}/api/tasks?

# Look for Firebase imports (should NOT be there)
Select-String -Path "web/oversight-hub/src/services/taskService.js" -Pattern "firebase/firestore"

# Should return: (no results)
```

**In browser:**

```javascript
// In browser console (F12):
// Import the new service
import { getTasks, createTask } from './src/services/taskService.js';

// Check API_BASE_URL is set
console.log(process.env.REACT_APP_API_URL); // Should show: http://localhost:8000

// Call function directly
getTasks()
  .then((tasks) => console.log('Tasks:', tasks))
  .catch((err) => console.error('Error:', err));
// Should return array of tasks, NOT timeout error
```

---

## 🎯 Success Indicators

### Quick Indicators (at a glance)

- ✅ No "TimeoutError" in console
- ✅ No Firebase errors
- ✅ Task appears in list after creation
- ✅ Database shows the task

### Detailed Indicators (verify each)

| Check                | Before Fix         | After Fix (Expected)            |
| -------------------- | ------------------ | ------------------------------- |
| **API endpoint**     | Non-existent       | http://localhost:8000/api/tasks |
| **Data source**      | Firebase           | PostgreSQL                      |
| **Console errors**   | 50+/minute         | 0                               |
| **Task persistence** | No                 | Yes                             |
| **List display**     | Empty              | Shows tasks                     |
| **Response time**    | 30s+ (timeout)     | <2s                             |
| **Database lookup**  | psql shows 0 tasks | psql shows all tasks            |

---

## 🚨 Troubleshooting Quick Fixes

### Issue: Still getting "TimeoutError: signal timed out"

**Quick Fixes in Order:**

1. Clear browser cache (DevTools → Application → Clear)
2. Hard refresh: `Ctrl+Shift+R`
3. Restart Oversight Hub: `Ctrl+C` in terminal, then `npm start`
4. Check backend running: `curl http://localhost:8000/api/health`
5. Check `.env.local` has `REACT_APP_API_URL=http://localhost:8000`

### Issue: Task created but not in list

**Quick Fixes in Order:**

1. Refresh page: `Ctrl+R`
2. Check browser console for errors: `F12` → Console tab
3. Check backend logs for POST request
4. Check PostgreSQL: `psql $env:DATABASE_URL -c "SELECT COUNT(*) FROM tasks;"`
5. Restart Oversight Hub if nothing else works

### Issue: Database connection error

**Quick Fixes in Order:**

1. Check DATABASE_URL is set: `echo $env:DATABASE_URL`
2. Test connection: `psql $env:DATABASE_URL -c "SELECT 1;"`
3. If psql not in PATH, add it: `$env:PATH += ";C:\Program Files\PostgreSQL\15\bin"`
4. Or use WSL if on Windows: `wsl -- psql $DATABASE_URL -c "SELECT 1;"`

---

## 📝 Sign-Off Checklist

After completing all tests, mark these:

- [ ] Phase 1: Services started successfully
- [ ] Phase 2: Browser cache cleared, hard refresh done
- [ ] Phase 3: Task created without errors
- [ ] Phase 4: Task appears in list
- [ ] Phase 5: Task verified in PostgreSQL
- [ ] Console: No timeout errors
- [ ] Code: taskService.js uses API calls (not Firebase)

**If ALL checked:** ✅ **FIX SUCCESSFUL - READY TO COMMIT**

**If ANY unchecked:** 🔍 **TROUBLESHOOT - See section above**

---

## 📚 Documents for Reference

- **QUICK_START_TASK_PIPELINE.md** - 3-minute quick start
- **TASK_PIPELINE_COMPLETE_FIX.md** - Full technical guide
- **scripts/Test-TaskPipeline.ps1** - Automated verification

---

## 🎯 Next Steps After Verification

### If Tests Pass (Expected)

1. Commit changes:
   ```bash
   git add .
   git commit -m "fix: replace firebase with fastapi backend for task pipeline"
   ```
2. Move to next todo item
3. Monitor for other pipeline issues

### If Tests Fail (Unexpected)

1. Check troubleshooting section
2. Review backend logs in detail
3. Check database configuration
4. If stuck, review attached documents

---

**Status:** Ready for testing  
**Expected Result:** All phases pass ✅  
**Estimated Time:** 15 minutes  
**Difficulty:** Easy - Mostly verification

**Begin testing now!** 🚀
