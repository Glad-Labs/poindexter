# Approval Workflow Testing with Logging

## Overview

This test suite verifies the complete approval workflow with comprehensive logging at every stage:

- **Frontend**: ResultPreviewPanel approval form submission
- **Service Layer**: unifiedStatusService routing and payload preparation
- **Backend API**: Endpoint handling and status validation
- **Database**: Persistence verification

## What Was Added

### 1. Enhanced Logging Locations

**Frontend (`web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx`):**
- Logs approval form submission with validation
- Tracks approval/rejection request preparation
- Logs service method calls and responses
- Shows detailed error messages

**Service Layer (`web/oversight-hub/src/services/unifiedStatusService.js`):**
- Logs approve() method entry and payload construction
- Tracks which endpoint is being attempted (new vs legacy)
- Shows endpoint fallback behavior
- Logs response from backend

**Backend Routes (`src/cofounder_agent/routes/task_routes.py`):**
- POST `/api/tasks/{task_id}/approve`: Entry point logging, status validation, DB update
- PUT `/api/tasks/{task_id}/status/validated`: Request logging, status change execution

**Backend Services (`src/cofounder_agent/services/enhanced_status_change_service.py`):**
- Logs task fetch and status validation
- Shows transition validation results
- Tracks history logging
- Logs database update results

**Database (`src/cofounder_agent/services/tasks_db.py`):**
- Logs metadata extraction and normalization
- Shows SQL query construction
- Verifies data persistence with response logging
- Logs extracted fields and their values

### 2. Test Script

**File**: `scripts/test-approval-workflow.py`

Comprehensive test that:
1. Creates a test task via API
2. Transitions to `awaiting_approval` status
3. Approves with detailed feedback
4. Queries database to verify:
   - Status changed to `approved`
   - Metadata contains approval feedback
   - Updated_at timestamp is recent
   - Status history is logged

## Running the Test

### Prerequisites

Ensure all services are running:

```bash
npm run dev  # Starts all three services
```

Verify services are healthy:

```bash
curl http://localhost:8000/health      # Backend API
curl http://localhost:3001/health      # Oversight Hub (optional)
curl http://localhost:3000              # Public Site (optional)
```

### Step 1: Run the Test Script

```bash
# Install test dependencies if needed
cd /path/to/glad-labs-website
pip install aiohttp asyncpg

# Run the test
python scripts/test-approval-workflow.py
```

### Step 2: Monitor Logs

While the test runs, monitor logs in separate terminals:

**Terminal 1 - Backend Logs:**
```bash
# Logs from the running co-founder agent (if using npm run dev, it's already shown)
# Otherwise, this logs to the terminal where you started the service
```

**Terminal 2 - Browser DevTools (for frontend logs):**
1. Open http://localhost:3001 (Oversight Hub)
2. Press F12 to open DevTools
3. Go to Console tab
4. You'll see all frontend logging with emoji prefixes

### Step 3: Monitor Database

In a separate terminal, you can query the database directly:

```bash
# Connect to PostgreSQL
psql $DATABASE_URL

# Check the most recent task
SELECT id, status, task_metadata, updated_at 
FROM content_tasks 
ORDER BY updated_at DESC 
LIMIT 1;

# Check status history
SELECT old_status, new_status, reason, created_at 
FROM status_history 
ORDER BY created_at DESC 
LIMIT 10;
```

## Expected Output

### Test Script Output

```
================================================================================
🧪 APPROVAL WORKFLOW TEST
================================================================================

📝 STEP 1: Creating test task...
   POST http://localhost:8000/api/tasks
   ✅ Task created: <task_id>

🔄 STEP 3: Transitioning to awaiting_approval status...
   ✅ Status updated

📖 Verifying DB after status change...
   ✅ Task updated
   - Status: awaiting_approval
   - Updated at: <timestamp>
   - Metadata keys: ['action', 'timestamp']

✅ STEP 4: Approving task with feedback...
   ✅ Approval submitted

================================================================================
🔍 STEP 5: CRITICAL - Verifying Database Persistence
================================================================================

✅ Task found in database

📊 Status Check:
   Expected: approved
   Actual: approved
   ✅ PASS

📊 Metadata Check:
   Metadata keys: ['action', 'approval_feedback', 'reviewer_notes', 'approved_at']
   ✅ action: approval
   ✅ approval_feedback: Great content! Minor grammar fix needed...
   ✅ reviewer_notes: Very good quality
   ✅ approved_at: <timestamp>
   ✅ PASS

📊 Timestamp Check:
   Updated at: <timestamp>
   ✅ PASS

================================================================================
✅ ALL CHECKS PASSED - Database persistence working correctly!
================================================================================
```

### Backend Console Logs

```
================================================================================
🔵 PUT /api/tasks/{task_id}/status/validated - ENTRY
   Task ID: <task_id>
   New Status: approved
   User: test-user@example.com
   Reason: Content approved by reviewer
   Metadata: {'action': 'approval', 'approval_feedback': '...', ...}
================================================================================

🔵 EnhancedStatusChangeService.validate_and_change_status()
   Task ID: <task_id>
   Target Status: approved
   User: test-user@example.com

🔍 Fetching current task...
✅ Current status: awaiting_approval
🔍 Validating transition: awaiting_approval → approved
✅ Transition valid

🔄 Updating task in database...
   Update data: {'status': 'approved', 'updated_at': <datetime>, 'task_metadata': {...}}
✅ UPDATE SUCCESS
   Status: approved
   Updated at: <timestamp>
   Metadata fields in response:
     - approval_feedback: Great content! Minor grammar fix needed...
     - reviewer_notes: Very good quality
     - approved_at: <timestamp>
```

### Frontend Console Logs

```
================================================================================
🔵 handleApprovalSubmit() ENTRY
   Approved: true
   Task ID: <task_id>
   Feedback: Great content! Minor grammar fix needed but overall excellent...
   Reviewer: reviewer-001

✅ All validations passed

📤 Sending APPROVAL request...
   Task ID: <task_id>
   Feedback length: <N> chars
   Reviewer: reviewer-001

🔄 Calling unifiedStatusService.approve()...

================================================================================
🔵 unifiedStatusService.approve()
   Task ID: <task_id>
   Feedback: Great content! Minor grammar fix needed but overall excellent...
   User ID: reviewer-001

🔄 Calling updateStatus() with APPROVED status...
   Payload: {...}

================================================================================
🔵 unifiedStatusService.updateStatus()
   Task ID: <task_id>
   New Status: approved
   Options: {...}

📤 Payload to send to backend:
   {
     "status": "approved",
     "updated_by": "reviewer-001",
     "reason": "Task approved",
     "metadata": {
       "action": "approve",
       "approval_feedback": "Great content! Minor grammar fix needed...",
       "timestamp": "<ISO timestamp>",
       "updated_from_ui": true,
       "feedback": "Great content! Minor grammar fix needed..."
     }
   }

🔄 Attempting NEW endpoint: PUT /api/tasks/<task_id>/status/validated
✅ NEW endpoint successful!
   Response: {"success": true, "task_id": "<task_id>", "message": "Status changed: awaiting_approval → approved", ...}
```

## Troubleshooting

### Test Fails - Status Not Updated to "approved"

**Issue**: Database shows status is still `awaiting_approval` after approval.

**Debugging Steps**:

1. Check backend logs for `EnhancedStatusChangeService.validate_and_change_status()` section
   - Look for validation errors
   - Check if transition validation passed

2. Check database update logs in `TasksDatabase.update_task()` section
   - Look for "UPDATE SUCCESS" or "UPDATE FAILED" message
   - Check if status field was included in update

3. Verify the endpoint is being called:
   - Check frontend logs for which endpoint (new vs legacy)
   - Check backend logs for endpoint entry message

### Test Fails - Metadata Not Persisted

**Issue**: Database shows empty or missing approval_feedback in task_metadata.

**Debugging Steps**:

1. Check `TasksDatabase.update_task()` logs:
   - Look for "Metadata fields present"
   - Check "Extracting fields from metadata to dedicated columns"
   - Look for "UPDATE SUCCESS" section

2. Check payload in frontend logs:
   - Verify `metadata.approval_feedback` is present
   - Verify payload structure matches expected schema

3. Query database directly:
   ```sql
   SELECT task_metadata FROM content_tasks WHERE id = '<task_id>';
   -- Check if approval_feedback is in the JSON
   ```

### Test Fails - Wrong Endpoint Called

**Issue**: Test uses legacy endpoint instead of new validated endpoint.

**Debugging Steps**:

1. Check frontend logs for endpoint attempt:
   - Look for "Attempting NEW endpoint" or "falling back to LEGACY"
   - If legacy fallback happened, check the reason (404, etc.)

2. Verify backend endpoint exists:
   ```bash
   curl -X OPTIONS http://localhost:8000/api/tasks/test/status/validated
   ```

3. Check task_routes.py for endpoint registration:
   ```bash
   grep -n "status/validated" src/cofounder_agent/routes/task_routes.py
   ```

### Database Query Not Returning Results

**Issue**: Task not found after approval.

**Debugging Steps**:

1. Verify task was created:
   ```sql
   SELECT * FROM content_tasks WHERE title LIKE '%Test Approval%';
   ```

2. Check status_history for transitions:
   ```sql
   SELECT * FROM status_history 
   WHERE task_id = '<task_id>' 
   ORDER BY created_at DESC;
   ```

3. Verify database connection:
   ```bash
   psql $DATABASE_URL -c "SELECT 1;"
   ```

## Manual Testing Alternative

If you prefer to test manually instead of running the script:

### Step 1: Create Task

1. Open http://localhost:3001 (Oversight Hub)
2. Create a new content task (blog post, article, etc.)
3. Wait for task to complete and reach "awaiting_approval" status
4. Note the task ID

### Step 2: Monitor Logs

1. Open browser DevTools (F12)
2. Go to Console tab
3. Filter by "approval" or "updateStatus"

### Step 3: Approve Task

1. Click on the task to open the result preview
2. Fill in the approval feedback (10-1000 characters)
3. Click "Approve & Publish"
4. Watch the console logs as the request is processed

### Step 4: Verify Database

```bash
# Connect to database
psql $DATABASE_URL

# Check the task status
SELECT status, task_metadata FROM content_tasks 
WHERE id = '<task_id>';

# Check history
SELECT old_status, new_status, reason FROM status_history 
WHERE task_id = '<task_id>' 
ORDER BY created_at DESC;
```

## Key Logging Points

### Frontend (3 locations)

1. **ResultPreviewPanel.jsx:458** - Form submission validation and service call
2. **unifiedStatusService.js:130** - Approve method wrapping and payload building
3. **unifiedStatusService.js:52** - Endpoint routing and request dispatch

### Backend (4 locations)

1. **task_routes.py:828** - PUT /status/validated entry point
2. **task_routes.py:1535** - POST /approve entry point (legacy)
3. **enhanced_status_change_service.py:28** - Status transition logic
4. **tasks_db.py:321** - Database update with field normalization

## Database Persistence Checklist

After approval, verify these in the database:

- [ ] `status` column = `'approved'`
- [ ] `updated_at` column has recent timestamp
- [ ] `task_metadata` JSON contains:
  - [ ] `action`: `'approval'`
  - [ ] `approval_feedback`: Contains the feedback text
  - [ ] `reviewer_notes`: Contains notes (if provided)
  - [ ] `approved_at`: Contains timestamp
- [ ] `status_history` table has entries for:
  - [ ] `awaiting_approval` → `approved` transition
  - [ ] Correct `reason` field
  - [ ] Recent `created_at` timestamp

## Next Steps

If all checks pass: ✅ Approval workflow is working correctly!

If some checks fail: 
1. Review the logs at the failing stage
2. Check the troubleshooting section above
3. Verify the code changes were applied correctly
4. Check database schema matches expectations
