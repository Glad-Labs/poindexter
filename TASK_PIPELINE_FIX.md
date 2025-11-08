# 🔧 Task Pipeline Fix - Database Mismatch

## 🚨 **ROOT CAUSE IDENTIFIED**

**The Problem:** Your task creation pipeline has a **database mismatch**:

- ✅ **Backend (FastAPI)** is properly configured to use **PostgreSQL** via `DatabaseService`
- ❌ **Frontend (Oversight Hub)** is still trying to use **Firebase/Firestore** (which doesn't exist)
- ❌ When Oversight Hub calls `/api/tasks`, it works, but then tries to fetch from Firestore which times out

**Timeline of Issues:**

1. Oversight Hub creates task via API call (succeeds)
2. Backend stores task in PostgreSQL (succeeds)
3. Oversight Hub tries to fetch tasks from Firebase (times out - Firebase not configured)
4. Frontend gets "TimeoutError: signal timed out" repeatedly

---

## ✅ **THE FIX**

### **Step 1: Replace Frontend Task Service (3 minutes)**

Replace `web/oversight-hub/src/services/taskService.js` to call the backend API instead of Firebase:

**File:** `web/oversight-hub/src/services/taskService.js`

```javascript
/**
 * Task Service - Uses FastAPI backend (PostgreSQL)
 *
 * This service communicates with the Co-Founder Agent backend API
 * which stores tasks in PostgreSQL database.
 */

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const API_TIMEOUT = 30000; // 30 seconds

/**
 * Fetch all tasks from the backend API
 * Uses database-level pagination for performance
 */
export const getTasks = async (offset = 0, limit = 20, filters = {}) => {
  try {
    const params = new URLSearchParams({
      offset: offset.toString(),
      limit: limit.toString(),
      ...(filters.status && { status: filters.status }),
      ...(filters.category && { category: filters.category }),
    });

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);

    try {
      const response = await fetch(`${API_BASE_URL}/api/tasks?${params}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        throw new Error(`Failed to fetch tasks: ${response.statusText}`);
      }

      const data = await response.json();
      return data.tasks || [];
    } catch (error) {
      clearTimeout(timeout);
      throw error;
    }
  } catch (error) {
    console.error('Error fetching tasks:', error);
    throw new Error(`Could not fetch tasks from backend: ${error.message}`);
  }
};

/**
 * Create a new task via the backend API
 */
export const createTask = async (taskData) => {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);

    try {
      const response = await fetch(`${API_BASE_URL}/api/tasks`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
        body: JSON.stringify(taskData),
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(
          errorData.detail?.message ||
            `Failed to create task: ${response.statusText}`
        );
      }

      const data = await response.json();
      return data.id; // Return task ID
    } catch (error) {
      clearTimeout(timeout);
      throw error;
    }
  } catch (error) {
    console.error('Error creating task:', error);
    throw new Error(`Could not create task: ${error.message}`);
  }
};

/**
 * Update task status via the backend API
 */
export const updateTask = async (taskId, updates) => {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);

    try {
      const response = await fetch(`${API_BASE_URL}/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
        body: JSON.stringify(updates),
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        throw new Error(`Failed to update task: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      clearTimeout(timeout);
      throw error;
    }
  } catch (error) {
    console.error('Error updating task:', error);
    throw new Error(`Could not update task: ${error.message}`);
  }
};

/**
 * Delete task via the backend API
 */
export const deleteTask = async (taskId) => {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);

    try {
      const response = await fetch(`${API_BASE_URL}/api/tasks/${taskId}`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        throw new Error(`Failed to delete task: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      clearTimeout(timeout);
      throw error;
    }
  } catch (error) {
    console.error('Error deleting task:', error);
    throw new Error(`Could not delete task: ${error.message}`);
  }
};

export default {
  getTasks,
  createTask,
  updateTask,
  deleteTask,
};
```

---

## 🔍 **VERIFICATION CHECKLIST**

### **Before Making Changes:**

```bash
# 1. Check if backend is running
curl -X GET http://localhost:8000/api/health

# Expected output:
# {"status":"healthy", ...}
```

### **After Making Changes:**

```bash
# 1. Clear browser cache
# - Dev Tools → Application → Clear Site Data

# 2. Restart Oversight Hub
npm start --workspace=web/oversight-hub

# 3. Create a new task via UI
# - Task name: "Test Task"
# - Topic: "Testing the pipeline"
# - Click "Create"

# 4. Check browser console for errors
# Should see: "Task created successfully" (no timeouts)

# 5. Verify task appears in the list
# Should see the new task in TaskManagement table

# 6. Check database directly (optional)
psql $DATABASE_URL -c "SELECT id, task_name, status FROM tasks LIMIT 5;"

# Expected output:
# id                                  | task_name      | status
# ----|----|----
# 550e8400-e29b-41d4-a716-446655440000 | Test Task      | pending
```

---

## 📊 **Data Flow After Fix**

```
┌─────────────────────────────────────┐
│  Oversight Hub (React)              │
│  TaskManagement.jsx                 │
└──────────────┬──────────────────────┘
               │
               │ Creates task via API
               ↓
┌─────────────────────────────────────┐
│  FastAPI Backend (Port 8000)        │
│  routes/task_routes.py              │
│  POST /api/tasks                    │
└──────────────┬──────────────────────┘
               │
               │ Stores in database
               ↓
┌─────────────────────────────────────┐
│  PostgreSQL Database                │
│  tasks table                        │
│  (id, task_name, status, ...)       │
└─────────────────────────────────────┘
               ↑
               │
               │ Fetches tasks
               │
┌──────────────┴──────────────────────┐
│  Oversight Hub (React)              │
│  TaskManagement.jsx                 │
│  useEffect → fetchTasks()           │
└─────────────────────────────────────┘
```

---

## ⚙️ **Environment Variables**

Make sure `web/oversight-hub/.env.local` has:

```bash
# Point to your backend
REACT_APP_API_URL=http://localhost:8000

# OR for production
REACT_APP_API_URL=https://api.railway.app
```

---

## 🚀 **Quick Start After Fix**

```bash
# 1. Update frontend service file
# (Copy the code from Step 1 above to taskService.js)

# 2. Restart services
npm run dev  # Or individual service: npm start --workspace=web/oversight-hub

# 3. Test in browser
# Go to http://localhost:3001
# Create a task
# Verify it appears in the list (no timeouts!)

# 4. If timeouts persist, check backend:
# Terminal where backend is running should show:
# POST /api/tasks - 201 Created
# GET /api/tasks - 200 OK
```

---

## 🐛 **If Still Having Issues**

### **Check Backend Logs:**

```bash
# Terminal running backend should show:
# [INFO] POST /api/tasks - Task created: {task_id}
# [INFO] GET /api/tasks - Fetching tasks with offset=0, limit=20

# If you see errors like:
# ERROR: DatabaseService not initialized
# → Solution: Restart backend, ensure DATABASE_URL is set
```

### **Check Frontend Logs:**

```bash
# Browser console should show:
# GET http://localhost:8000/api/tasks?offset=0&limit=20
# Response: {tasks: [...], total: 1}

# If you see CORS errors:
# → Solution: Check backend CORS settings in main.py
```

### **Check Database Connection:**

```powershell
# Test connection
psql $env:DATABASE_URL -c "SELECT COUNT(*) FROM tasks;"

# If connection fails:
# → Check DATABASE_URL is set correctly
# → Check PostgreSQL service is running
```

---

## ✅ **Expected Behavior After Fix**

| Action          | Before Fix                          | After Fix                      |
| --------------- | ----------------------------------- | ------------------------------ |
| Create task     | API returns 201, but task not saved | Task saved to PostgreSQL ✅    |
| Fetch tasks     | TimeoutError repeatedly             | Returns tasks in <2 seconds ✅ |
| See tasks in UI | Blank list                          | Tasks appear in table ✅       |
| Update task     | Error                               | Updates successfully ✅        |
| Delete task     | Error                               | Deletes from DB ✅             |

---

## 📝 **Files to Update**

1. ✅ `web/oversight-hub/src/services/taskService.js` - Replace with code above
2. ✅ No other files need changes (backend already uses PostgreSQL)
3. Optional: Update `.env.local` to point to correct API URL

---

## 🎯 **Next Steps**

1. **Apply the fix** (replace taskService.js)
2. **Restart services** (npm start)
3. **Test in browser** (create task, verify in list)
4. **Check database** (optional: psql to verify task exists)
5. **Monitor backend logs** (watch terminal for POST/GET requests)

**Estimated time to fix: 5-10 minutes** ⏱️

---

**Questions?** Check the backend logs or database directly to verify the pipeline is working.
