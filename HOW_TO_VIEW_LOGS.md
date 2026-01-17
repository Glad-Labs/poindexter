# How to Monitor and View Approval Workflow Logs

## Overview

We've added comprehensive logging to track the entire approval workflow from frontend to database. This guide shows you where to find and monitor these logs.

## 1. Frontend Console Logs (Most Visible)

### How to Access:
1. Open http://localhost:3001 (Oversight Hub)
2. Press **F12** to open Developer Tools
3. Click **Console** tab
4. Find the task with `awaiting_approval` status
5. Click to open result preview
6. Click "Approve & Publish"

### What You'll See:
When approval is triggered, you'll see console output like:

```
================================================================================
[TEST] handleApprovalSubmit() ENTRY
   Approved: true
   Task ID: <uuid>
   Feedback: Great content! ...
   Reviewer: <reviewer-id>

[SUCCESS] All validations passed

[SEND] APPROVAL request...
   Task ID: <uuid>
   Feedback length: 45 chars
   Reviewer: test-user

[CALL] unifiedStatusService.approve()...
   Payload contains: action, approval_feedback, timestamp, updated_from_ui

[ENDPOINT] PUT /api/tasks/.../status/validated
================================================================================
```

### Key Log Sections:
- **Entry**: Form submission with validation
- **Validation**: Feedback length, reviewer ID checks
- **Service Call**: Shows unifiedStatusService.approve()
- **Endpoint**: Shows which API endpoint was called
- **Response**: Backend response with success/failure

## 2. Backend Logs (Terminal)

### Where to Find:
The backend logs are shown in the terminal where you ran `npm run dev`

Look for output starting with:
```
INFO:     127.0.0.1:PORT - "PUT /api/tasks/... HTTP/1.1" 200 OK
```

### Backend Log Sections:

**Section 1 - API Route Entry** (task_routes.py):
```
================================================================================
[ROUTE] PUT /api/tasks/{task_id}/status/validated - ENTRY
   Task ID: 705cd74b-3f9a-48a5-b0b7-a6719529c82d
   New Status: approved
   User: test-user@example.com
   Reason: Content approved by reviewer
   Metadata: {'action': 'approval', ...}
================================================================================
```

**Section 2 - Status Validation** (enhanced_status_change_service.py):
```
[SERVICE] EnhancedStatusChangeService.validate_and_change_status()
   Task ID: 705cd74b-3f9a-48a5-b0b7-a6719529c82d
   Target Status: approved
   User: test-user@example.com
   
   [FETCH] Fetching current task...
   [SUCCESS] Current status: awaiting_approval
   
   [VALIDATE] Validating transition: awaiting_approval -> approved
   [SUCCESS] Transition valid
```

**Section 3 - Database Update** (tasks_db.py):
```
[DATABASE] TasksDatabase.update_task()
   Task ID: 705cd74b-3f9a-48a5-b0b7-a6719529c82d
   
   [EXTRACT] Extracting metadata fields...
   [SUCCESS] Fields extracted: 4 items
   
   [BUILD] Building SQL UPDATE clause...
   [SQL] UPDATE content_tasks SET status=$1, task_metadata=$2, ...
   
   [EXECUTE] Executing UPDATE query...
   [SUCCESS] UPDATE SUCCESS
      Status: approved
      Metadata: {'approval_feedback': '...', 'reviewer_notes': '...', ...}
```

## 3. Database Verification

After approval, you can query the database directly to see the persisted data:

### Connect to Database:
```bash
# Using the DATABASE_URL from .env.local
psql $DATABASE_URL
```

### Check Task Status:
```sql
-- Find the most recently updated task
SELECT 
    id, 
    status, 
    task_metadata, 
    updated_at 
FROM content_tasks 
ORDER BY updated_at DESC 
LIMIT 1;
```

### Expected Output:
```
 id |  status  |                          task_metadata                           |         updated_at
----+----------+---------------------------------------------------------------------+----------------------------
  5 | approved | {"approval_feedback": "Great content! ...", "reviewer_notes": ...} | 2026-01-17 09:33:22.391+00
(1 row)
```

### Check Specific Fields in Metadata:
```sql
-- Extract approval_feedback from metadata
SELECT 
    id,
    status,
    task_metadata->'approval_feedback' AS feedback,
    task_metadata->'reviewer_notes' AS notes,
    task_metadata->'approved_at' AS approved_timestamp
FROM content_tasks 
WHERE id = 5;
```

## 4. Search Frontend Logs by Keyword

In the browser console, you can filter logs:

1. In DevTools console, type `%s` prefix to search
2. Or use Ctrl+F in console to search
3. Search for:
   - "handleApprovalSubmit" - Approval form submission
   - "unifiedStatusService" - Service layer
   - "approve()" - Approve method calls
   - "updateStatus" - Status update calls
   - "[SUCCESS]" - Successful operations
   - "[FAILED]" - Failed operations

## 5. Complete Approval Workflow Flow

### Step 1: Form Submission (Frontend)
```
→ Click "Approve & Publish" button
→ handleApprovalSubmit() logs entry
→ Validates feedback (10-1000 chars)
→ Validates reviewer ID
→ Calls unifiedStatusService.approve()
```

### Step 2: Service Routing (Frontend)
```
→ unifiedStatusService.approve() logs call
→ Calls updateStatus() with "approved" status
→ Builds payload with metadata including approval_feedback
→ Attempts new endpoint: PUT /api/tasks/{id}/status/validated
→ If 404, falls back to legacy endpoint
→ Makes HTTP request to backend
```

### Step 3: API Route Handling (Backend)
```
→ task_routes.py receives PUT request
→ Logs entry with all request details
→ Calls EnhancedStatusChangeService.validate_and_change_status()
→ Returns success response to frontend
```

### Step 4: Status Validation & Change (Backend)
```
→ EnhancedStatusChangeService receives change request
→ Fetches current task from database
→ Validates transition: awaiting_approval → approved
→ Calls db_service.update_task() with new status + metadata
→ (Optional) Logs change to status_history table
```

### Step 5: Database Persistence (Backend)
```
→ TasksDatabase.update_task() receives update
→ Extracts nested fields from metadata
→ Builds SQL UPDATE query
→ Executes: UPDATE content_tasks SET status='approved', task_metadata={...}
→ Returns updated task record
→ Logs confirmation with persisted values
```

### Step 6: Confirmation (Frontend)
```
→ Frontend receives success response
→ Shows alert: "Task approved successfully!"
→ Closes approval modal
→ Parent component will refresh task list
```

## 6. Troubleshooting: What If Something Goes Wrong?

### ❌ No Console Logs Appearing

**Check**:
1. DevTools Console is open (F12, Console tab)
2. Not filtered to error level only (should show all levels)
3. Refresh page after opening DevTools
4. Check network tab to see if request was sent

**Solution**:
1. Refresh browser
2. Make sure DevTools is open BEFORE clicking Approve
3. Check Network tab for the API call

### ❌ API Returns 401 Unauthorized

**Check**:
1. Auth token may have expired
2. API requires authentication
3. Oversight Hub session expired

**Solution**:
1. Logout and login again
2. Check if auth token is in local storage: DevTools > Application > Local Storage
3. Look for `auth_token` or `access_token`

### ❌ Database Shows Old Status

**Check**:
1. API call might have failed (check network tab for 4xx/5xx)
2. Update query might have failed
3. Looking at wrong task ID

**Solution**:
```sql
-- Check for errors in recent updates
SELECT id, status, updated_at FROM content_tasks 
ORDER BY updated_at DESC LIMIT 5;
```

### ❌ Metadata Fields Are Empty

**Check**:
1. Approval payload doesn't include metadata
2. Metadata extraction in database failed
3. SQL UPDATE didn't include task_metadata

**Solution**:
1. Check frontend logs for payload content
2. Check backend logs for "UPDATE SUCCESS" section
3. Query database to see what was actually saved:
   ```sql
   SELECT task_metadata FROM content_tasks WHERE id = <task_id>;
   ```

## 7. Real-Time Log Monitoring

### Option A: Split Screen
1. Terminal on left: `npm run dev` (shows backend logs)
2. Browser on right: http://localhost:3001 (frontend with DevTools open)
3. Perform approval workflow
4. Watch logs appear in real time

### Option B: Log Tailing
```bash
# In a separate terminal, tail backend logs
# (if using log file instead of console output)
tail -f server.log | grep -i approval
```

### Option C: Browser Console with Filters
In DevTools console, search for approval-related logs:
```javascript
// See all approval-related logs
console.log("Search for: handleApprovalSubmit, updateStatus, approve");
```

## 8. Expected vs Actual Comparison

### Expected Log Flow:
1. Frontend: Form validation ✓
2. Frontend: Service call ✓
3. Frontend: Endpoint selection ✓
4. Backend: Route entry ✓
5. Backend: Status validation ✓
6. Backend: Database update ✓
7. Backend: Response return ✓
8. Frontend: Success confirmation ✓

### If Steps Are Missing:
- **Missing Frontend Logs**: Check console is open, browser permissions
- **Missing Backend Logs**: Check terminal output, might be scrolled up
- **Missing DB Confirmation**: Query database directly
- **Logs Show ERROR**: Read error message carefully for specific issue

## 9. Log Files Reference

### Files with Logging:
- **Frontend**:
  - `/web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx` - Approval form
  - `/web/oversight-hub/src/services/unifiedStatusService.js` - Service layer

- **Backend**:
  - `/src/cofounder_agent/routes/task_routes.py` - API endpoints
  - `/src/cofounder_agent/services/enhanced_status_change_service.py` - Status logic
  - `/src/cofounder_agent/services/tasks_db.py` - Database operations

### Each File Has Logging Around:
- Entry points (function start)
- Data validation
- API requests/responses
- Database queries
- Error handling
- Success confirmations

## 10. Common Log Messages

### Success Messages:
```
[SUCCESS] Connected to database
[PASS] All validations passed
[SUCCESS] Status updated
[SUCCESS] UPDATE SUCCESS
```

### Info Messages:
```
[INFO] Sending APPROVAL request
[INFO] Calling unifiedStatusService.approve()
[INFO] Extracting metadata fields
```

### Check Messages:
```
[CHECK] Status: approved
[CHECK] Metadata Persistence
[CHECK] Timestamp
```

### Failed Messages:
```
[FAILED] 401 - Unauthorized
[FAILED] Task not found
[FAILED] Some checks failed
```

---

**Next Step**: Open http://localhost:3001, test an approval, and watch these logs appear in real time!
