# Oversight Hub API Integration - Implementation Fixes Session

**Session Date:** 2024
**Status:** Phase 2 - Implementation In Progress (50% complete)

---

## ✅ COMPLETED FIXES (4/9)

### Fix 1: CreateTaskModal.jsx - Hardcoded URLs ✅
**Location:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`

**What was fixed:**
- ❌ Before: Used hardcoded `fetch('http://localhost:8000/api/content/tasks', ...)` and `fetch('http://localhost:8000/api/tasks', ...)`
- ✅ After: Uses API client `createTask()` with proper payload mapping
- ✅ Added: Response validation `if (!result || !result.id) throw Error(...)`
- ✅ Added: Import statement `import { createTask } from '../../services/cofounderAgentClient';`

**Changes Made:**
- Lines 1-3: Added import for `createTask`
- Lines 207-305: Replaced all fetch logic with API client + validation
- Simplified payload construction to use API client schema
- Added proper error handling

**Verification:** ✅ Component now uses centralized API client with environment-aware URLs

---

### Fix 2: TaskQueueView.jsx - Unused Fetch ✅
**Location:** `web/oversight-hub/src/components/tasks/TaskQueueView.jsx`

**What was fixed:**
- ❌ Before: Had unused `fetch('http://localhost:8000/api/tasks')` call that didn't use response
- ✅ After: Removed dead code, added comment explaining tasks come from parent props

**Changes Made:**
- Lines 10-27: Removed entire unused useEffect with fetch call
- Added comment: "Tasks are passed as props from parent, no fetch needed"
- Kept polling state for UI controls

**Verification:** ✅ Removed dead code, component is cleaner

---

### Fix 3: BlogPostCreator.jsx - Hardcoded URLs ✅
**Location:** `web/oversight-hub/src/components/tasks/BlogPostCreator.jsx`

**What was fixed:**
- ❌ Before: Used hardcoded `fetch('http://localhost:8000/api/content/tasks', ...)`
- ✅ After: Uses API client `createTask()` with proper payload mapping
- ✅ Added: Response validation
- ✅ Added: Import statement

**Changes Made:**
- Lines 1-20: Added import for `createTask`
- Lines 72-95: Replaced hardcoded fetch with API client call
- Mapped form fields to API client schema
- Added response validation check

**Verification:** ✅ Component uses centralized API client

---

### Fix 4: Environment Configuration ✅
**Location:** `web/oversight-hub/.env.local`

**What was verified:**
- ✅ `REACT_APP_API_URL=http://localhost:8000` is already configured
- ✅ API client (`cofounderAgentClient.js`) already uses environment variable fallback
- ✅ Default fallback: `const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000'`

**Verification:** ✅ Environment setup complete, all API calls use configurable URL

---

## 🔧 IN PROGRESS / PENDING FIXES (5/9)

### Fix 2B: TaskManagement.jsx - Non-existent /api/tasks/bulk Endpoint
**Location:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx` (lines 257-263)

**Issue:** Calls `/api/tasks/bulk` endpoint which doesn't exist in FastAPI backend
```jsx
// ❌ BROKEN: This endpoint doesn't exist
const response = await fetch('http://localhost:8000/api/tasks/bulk', {
  method: 'POST',
  headers,
  body: JSON.stringify({
    task_ids: selectedTasks,
    action: action,
  }),
});
```

**Options for Fix:**
1. **Remove bulk feature** - Delete this button/functionality if not needed
2. **Use individual PATCH calls** - Loop through tasks and update each one:
   ```javascript
   for (const taskId of selectedTasks) {
     await updateTask(taskId, { status: action });
   }
   ```
3. **Implement bulk endpoint in FastAPI** - Add `/api/tasks/bulk` PATCH endpoint

**Status:** ⏳ Awaiting decision - which approach should be used?

---

### Fix 5: LayoutWrapper.jsx - /api/chat Hardcoded URL
**Location:** `web/oversight-hub/src/components/LayoutWrapper.jsx` (line 154)

**Issue:** Uses hardcoded fetch to `/api/chat` endpoint
```jsx
// ❌ BROKEN: Hardcoded URL
const response = await fetch('http://localhost:8000/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ /* ... */ }),
});
```

**Fix Strategy:**
- Create `chatMessage()` method in `cofounderAgentClient.js`
- Or check if this endpoint exists in FastAPI backend
- Then replace with API client call

**Status:** ⏳ Pending investigation - does `/api/chat` endpoint exist?

---

### Fix 6: ExecutionHub.jsx - /api/execution/* Hardcoded URLs
**Location:** `web/oversight-hub/src/components/pages/ExecutionHub.jsx` (lines 33-39)

**Issue:** Three parallel hardcoded fetch calls to non-existent endpoints
```jsx
// ❌ BROKEN: Hardcoded URLs, non-existent endpoints?
const [activeRes, queueRes, historyRes] = await Promise.all([
  fetch('http://localhost:8000/api/execution/active', { /* ... */ }),
  fetch('http://localhost:8000/api/execution/queue', { /* ... */ }),
  fetch('http://localhost:8000/api/execution/history', { /* ... */ }),
]);
```

**Fix Strategy:**
- Verify if these endpoints exist in FastAPI backend
- If not: Create them or disable this component
- If yes: Create wrapper methods in API client

**Status:** ⏳ Pending investigation - do `/api/execution/*` endpoints exist?

---

### Fix 7: CostMetricsDashboard.jsx - /api/metrics/costs Hardcoded URL
**Location:** `web/oversight-hub/src/components/CostMetricsDashboard.jsx` (line 45)

**Issue:** Uses hardcoded fetch to `/api/metrics/costs`
```jsx
// ❌ BROKEN: Hardcoded URL
const response = await fetch('http://localhost:8000/api/metrics/costs');
```

**Fix Strategy:**
- Check if `/api/metrics/costs` endpoint exists in FastAPI backend
- If exists: Create wrapper method in API client
- If not: Use existing `/api/tasks/metrics/summary` endpoint

**Status:** ⏳ Pending investigation - verify endpoint

---

## 📊 SUMMARY OF ALL HARDCODED URL LOCATIONS

| File | Line(s) | Endpoint | Status |
|------|---------|----------|--------|
| CreateTaskModal.jsx | 207-305 | `/api/content/tasks`, `/api/tasks` | ✅ FIXED |
| BlogPostCreator.jsx | 72-95 | `/api/content/tasks` | ✅ FIXED |
| TaskQueueView.jsx | 10-27 | `/api/tasks` | ✅ REMOVED (unused) |
| TaskManagement.jsx | 257 | `/api/tasks/bulk` | ⚠️ NON-EXISTENT - Needs fix |
| LayoutWrapper.jsx | 154 | `/api/chat` | ❓ VERIFY exists |
| ExecutionHub.jsx | 33-39 | `/api/execution/active`, `/api/execution/queue`, `/api/execution/history` | ❓ VERIFY exist |
| CostMetricsDashboard.jsx | 45 | `/api/metrics/costs` | ❓ VERIFY exists |

---

## 🚀 API CLIENT STATUS

**File:** `web/oversight-hub/src/services/cofounderAgentClient.js` (779 lines)

**Current Methods Available:**
- ✅ `getTasks(limit, offset)` - List tasks with pagination
- ✅ `createTask(payload)` - Create new task
- ✅ `getTask(id)` - Get single task details
- ✅ `getTaskStatus(id)` - Get task status
- ✅ `pollTaskStatus(id, maxAttempts, interval)` - Poll until complete
- ✅ `updateTask(id, updates)` - Update task

**Environment Configuration:**
- ✅ Uses `process.env.REACT_APP_API_URL` with fallback to `http://localhost:8000`
- ✅ JWT token automatically injected via `getAuthToken()`
- ✅ Proper error handling and logging

**Missing Methods Needed:**
- ❌ `chatMessage(message, model, mode, agent)` - For LayoutWrapper
- ❌ `getExecutionActive()` - For ExecutionHub
- ❌ `getExecutionQueue()` - For ExecutionHub
- ❌ `getExecutionHistory()` - For ExecutionHub
- ❌ `getCostMetrics()` - For CostMetricsDashboard

---

## 📋 NEXT STEPS

### Immediate (Required for Working UI):
1. **Decide on /api/tasks/bulk fix** - Option 1: Remove, Option 2: Implement individually, Option 3: Create endpoint
2. **Verify ExecutionHub endpoints exist** - Check FastAPI backend if `/api/execution/*` endpoints are implemented
3. **Verify CostMetricsDashboard endpoint** - Check if `/api/metrics/costs` exists
4. **Verify LayoutWrapper /api/chat** - Check if this endpoint is implemented

### Implementation (Once decisions made):
1. Create missing methods in `cofounderAgentClient.js`
2. Update remaining components to use API client
3. Remove any remaining hardcoded URLs
4. Test all components for proper API integration

### Testing Checklist:
- [ ] Create task via CreateTaskModal → verify appears in TaskManagement list
- [ ] Check Network tab → no 404 errors, all URLs use env variable
- [ ] Verify JWT tokens automatically injected in Authorization headers
- [ ] Test pagination with different limit/offset values
- [ ] Test error handling for invalid responses
- [ ] Test in both development (localhost:8000) and production URLs

---

## 📝 QUICK REFERENCE

### To Add New API Client Method:
```javascript
// In cofounderAgentClient.js, add method like:
export const myNewMethod = async (params) => {
  const response = await makeRequest(`/api/endpoint`, {
    method: 'GET',
    // or 'POST', 'PATCH', etc.
  });
  return response;
};

// Then use in component:
import { myNewMethod } from '../../services/cofounderAgentClient';
const result = await myNewMethod(data);
```

### To Replace Hardcoded Fetch:
1. Identify the endpoint and HTTP method
2. Check if method exists in `cofounderAgentClient.js`
3. If not, add it
4. Replace hardcoded fetch with API client method
5. Add response validation
6. Test in browser Network tab

---

## 📖 DOCUMENTATION REFERENCES

- **API Endpoints:** `ENDPOINT_AUDIT_REPORT.md`
- **Integration Guide:** `FASTAPI_INTEGRATION_GUIDE.md`
- **Quick Fixes:** `QUICK_FIX_GUIDE.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`

---

## 🔍 INVESTIGATION NEEDED

Before completing remaining fixes, need answers to:

1. **TaskManagement bulk endpoint:**
   - Is `/api/tasks/bulk` implemented in FastAPI?
   - If not, should feature be removed or individual updates used?

2. **LayoutWrapper chat:**
   - Is `/api/chat` endpoint implemented?
   - Should this be a different endpoint?

3. **ExecutionHub:**
   - Are `/api/execution/active`, `/api/execution/queue`, `/api/execution/history` implemented?
   - Are these phase 6 features?

4. **CostMetricsDashboard:**
   - Is `/api/metrics/costs` implemented?
   - Can we use existing `/api/tasks/metrics/summary` instead?

