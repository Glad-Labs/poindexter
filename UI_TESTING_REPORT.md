# UI Testing Report - March 1, 2026

**Test Date:** March 1, 2026
**Test Duration:** 2 hours
**Services Tested:** Approval Queue UI, Posts API, Public Site API

---

## Executive Summary

Found **3 critical bugs** and **1 limitation** affecting the approval workflow and content publishing pipeline:

1. **🔴 CRITICAL**: auto_publish parameter not being recognized from ApprovalRequest
2. **🔴 CRITICAL**: Approval response missing post_id and post_slug fields
3. **🟡 HIGH**: Reject endpoint returns 'failed_revisions_requested' instead of 'rejected'
4. **🟠 MEDIUM**: Old tasks lack qa_feedback (not populated for pre-fix tasks)

---

## Bug Details

### Bug #1: Auto-Publish Parameter Not Recognized [CRITICAL]

**Severity:** CRITICAL - Breaks auto-publish functionality
**Status:** UNFIXED

**Symptoms:**

- Sending `auto_publish=true` in approval request
- Task status returns as 'approved' instead of 'published'
- No post created in posts table
- post_id and post_slug remain null

**Test Results:**

```
Request: POST /api/tasks/{id}/approve
Body: {"approved": true, "auto_publish": true}

Response Status: 200
Response Body: {
  "status": "approved",  <- Expected "published"
  "post_id": null,       <- Expected UUID
  "post_slug": null      <- Expected slug string
}
```

**Root Cause:** TBD

- ApprovalRequest model has `auto_publish: bool = False` field (present)
- Request body sends `auto_publish: true`
- But response indicates `auto_publish` is False

**Investigation Notes:**

- Multiple approve_task endpoints exist (approval_routes.py and task_routes.py)
- approval_routes.py is registered first and should be called
- Pydantic model has the field defined
- Debug logging added but backend service crashed, needs restart

**Impact:**

- Users cannot auto-publish content when approving
- Full approval → publish workflow broken
- Requires manual publish step

---

### Bug #2: Approval Response Missing Post IDs [CRITICAL]

**Severity:** CRITICAL - Prevents user feedback on successful publishing
**Status:** UNFIXED

**Symptoms:**

- Approval endpoint returns 200 OK
- Response includes task_id, status, approval_status
- Response MISSING: post_id, post_slug, published_url
- Users don't know if post creation succeeded

**Test Results:**

```
Response Keys: ['task_id', 'status', 'approval_status', 'approval_date',
                'approval_timestamp', 'approved_by', 'feedback', 'message',
                'next_action']

Expected Keys (missing):
  - post_id
  - post_slug
  - published_url
```

**Expected Response Format:**

```json
{
  "task_id": "...",
  "status": "published",
  "post_id": "e17a472f-46a7-49dc-8bb7-bda5eed1b460",
  "post_slug": "how-testing-cost-metrics-fix-can-optimize-your-pro-74d090a1",
  "published_url": "/posts/how-testing-cost-metrics-fix-can-optimize-your-pro-74d090a1"
}
```

**Impact:**

- React UI cannot show "Published at: /posts/..." message
- No feedback to user that post creation succeeded
- No URL to share published content

---

### Bug #3: Reject Returns Wrong Status [HIGH]

**Severity:** HIGH - Incorrect status value returned
**Status:** UNFIXED

**Symptoms:**

- POST /api/tasks/{id}/reject with reason and feedback
- Endpoint returns status code 200
- Response status field: `'failed_revisions_requested'` instead of `'rejected'`

**Test Results:**

```
Request: POST /api/tasks/{id}/reject
Body: {"reason": "Test", "feedback": "Test feedback"}

Response: 200 OK
Response Status: "failed_revisions_requested"  <- Expected "rejected"
```

**Expected:**

```python
response_status = "rejected"  # or "rejection_requested"
```

**Root Cause:**

- The reject_task endpoint in approval_routes.py is correctly setting status
- But the old endpoint might be returning a different status value
- Inconsistency between documented endpoint behavior and actual response

**Impact:**

- React UI may not properly detect rejected tasks
- Confusion about task status field values
- May break bulk operations expecting status='rejected'

---

### Issue #4: QA Feedback Not Populated for Old Tasks [MEDIUM]

**Severity:** MEDIUM - Limitation of fix, not a bug

**Symptoms:**

- Existing 78 tasks have qa_feedback = null
- New tasks created after QA feedback fix should have it
- Users see no feedback for existing content

**Test Results:**

```
Task 1 (2d55032d): quality_score=62, qa_feedback=None
Task 2 (117fc046): quality_score=58, qa_feedback=None
Task 3 (9bb512cc): quality_score=63, qa_feedback=None
```

**Root Cause:**

- Code change to generate qa_feedback was applied on 2026-03-01 ~05:17
- Existing tasks were created before the fix
- Only new tasks will have qa_feedback

**Verification Needed:**

- Create a new task and check if qa_feedback is populated
- Verify content_router_service.py line 587 changed to LLM_BASED

**Impact:**

- Existing tasks lack QA feedback for users
- New tasks should have it going forward
- May need batch update script to populate missing feedback

---

## API Response Schema Consistency

### ✅ Verified Working

**GET /api/tasks - Response Format:**

```json
{
  "tasks": [...],
  "total": 4,
  "offset": 0,
  "limit": 10
}
```

**GET /api/posts - Response Format:**

```json
{
  "data": [
    {
      "id": "...",
      "title": "...",
      "slug": "...",
      "featured_image_url": "...",
      ...
    }
  ],
  "meta": {...}
}
```

**POST /api/tasks/bulk-approve - Works correctly:**

- Returns { "approved_count": 1, "failed_count": 0, ... }
- Properly handles invalid task IDs

---

## Test Execution Results

| Test | Status | Notes |
|------|--------|-------|
| Fetch pending approval tasks | ✅ PASS | Found 4 tasks |
| Get task details | ✅ PASS | Fields present |
| Test reject endpoint | ⚠️ FAIL | Wrong status value |
| Check posts endpoint | ✅ PASS | 10 posts found |
| Bulk approve endpoint | ✅ PASS | Works with fake IDs |
| Error handling (404) | ✅ PASS | Correct HTTP status |
| Auto-publish workflow | ❌ FAIL | auto_publish not recognized |
| Post creation on approve | ❌ FAIL | No post_id in response |
| QA feedback on approval | ⚠️ SKIP | Old tasks predate fix |

---

## Recommendations

### Immediate (Critical Path)

1. **Fix auto_publish Parameter Parsing**
   - Add additional debug logging to approve_task function
   - Verify ApprovalRequest is being instantiated correctly
   - Check if there's a route conflict between approval_routes and task_routes
   - Ensure Content-Type header is application/json

2. **Add post_id/post_slug to Approval Response**
   - Test the response-building code in approval_routes.py lines 345-365
   - Verify post_id is actually being saved to database during auto-publish
   - Return post creation details in response

3. **Fix Reject Status Value**
   - Change response status from 'failed_revisions_requested' to 'rejected'
   - Update any tests expecting the old status value
   - Check React UI for status handling

### Short Term (Quality)

1. **Populate QA Feedback for New Tasks**
   - Verify LLM-based evaluation is working
   - Create test task and confirm qa_feedback is populated
   - Consider batch job to populate missing feedback for existing tasks

2. **Improve Error Messages**
   - Add more descriptive error messages for failed operations
   - Include helpful guidance for common errors

---

## Files to Review

| File | Issue | Priority |
|------|-------|----------|
| `src/cofounder_agent/routes/approval_routes.py` | auto_publish not recognized, response missing fields | CRITICAL |
| `src/cofounder_agent/routes/approval_routes.py` | reject status wrong | HIGH |
| `src/cofounder_agent/services/content_router_service.py` | QA feedback generation | MEDIUM |
| `web/oversight-hub/src/components/tasks/ApprovalQueue.jsx` | Expects post_id in response | CRITICAL |

---

## Next Steps

1. Restart backend with debug logging enabled
2. Test auto_publish parameter parsing with logs
3. Fix auto_publish recognition issue
4. Update response schema to include post creation details
5. Fix reject endpoint status value
6. Verify new tasks have qa_feedback
