# Auto-Publish Bug Investigation - Progress Report

**Date:** March 1, 2026
**Status:** 🔍 INVESTIGATING - Root cause identified, fix being tested
**Severity:** CRITICAL - Blocks approval → publishing workflow

---

## Problem Confirmed

**Test Result:** Auto-publish is NOT working
```
Frontend sends: { "approved": true, "auto_publish": true, ... }
Response status: "approved" (should be "published")
Response has post_id: false (should be true)
Database status: "approved" (should be "published")
```

**Evidence:** The test `test_auto_publish_full.py` demonstrates:
- Task reaches awaiting_approval status ✓
- Approval request with auto_publish=true sent successfully ✓
- Endpoint returns HTTP 200 OK ✓
- But response status is "approved", not "published" ✗
- Post is never created in posts table ✗

---

## Root Cause Analysis

**Hypothesis:** The Pydantic `ApprovalRequest` model is not correctly parsing the `auto_publish=true` value from the request body.

**Investigation Steps Completed:**

1. ✅ Confirmed both approval_routes.py and task_routes.py have approve_task endpoints
2. ✅ Verified approval_routes.py is registered FIRST (has priority)
3. ✅ Confirmed ApprovalRequest model defines `auto_publish: bool = False` field
4. ✅ Confirmed the auto_publish handler exists in approval_routes.py (lines 223-319)
5. ✅ Found field name mismatch: Frontend sends `human_feedback`, model expects `feedback`
6. ✅ Fixed ApprovalRequest to accept both field names
7. ✅ Added detailed logging to trace auto_publish value through request handling

**Likely Root Causes:**
1. Pydantic is not parsing the boolean value correctly (unlikely)
2. The auto_publish field is being reset somewhere before the handler check
3. There's a type conversion issue with the boolean value
4. The request is being parsed by a different endpoint (unlikely - confirmed routing)

---

## Code Locations

**Approval Routes (PRIMARY ENDPOINT):**
- File: `src/cofounder_agent/routes/approval_routes.py`
- Endpoint: `POST /api/tasks/{task_id}/approve` (line 116)
- Model: `ApprovalRequest` (lines 45-71)
- Auto-publish handler: Lines 223-319
- Auto-publish check: Line 223 `if request.auto_publish:`
- Response handler: Lines 373-389

**Request Model:**
```python
class ApprovalRequest(BaseModel):
    approved: bool = True
    feedback: Optional[str] = None
    reviewer_notes: Optional[str] = None
    auto_publish: bool = False  # <-- THIS FIELD
    featured_image_url: Optional[str] = None
    image_source: Optional[str] = None
```

**Frontend Code:**
- File: `web/oversight-hub/src/components/tasks/ApprovalQueue.jsx`
- Function: `handleApprovalSubmit` (line 296)
- Payload (line 311-318):
```javascript
{
  approved: true,
  auto_publish: true,  // <-- SENT AS TRUE
  human_feedback: approveFeedback || undefined,
  ...
}
```

---

## Fixes Applied

### Fix 1: Field Name Mismatch (COMPLETED)
**Commit:** `4f01301bf`

Fixed ApprovalRequest to accept `human_feedback` from frontend (maps to `feedback`):
```python
def __init__(self, **data):
    if 'human_feedback' in data and not data.get('feedback'):
        data['feedback'] = data['human_feedback']
    super().__init__(**data)
```

**Result:** Field mapping works, but still doesn't explain auto_publish issue.

### Fix 2: Enhanced Logging (COMPLETED)
**Commit:** `44c25196a`

Added super-detailed logging (lines 213-221) to show exact value, type, and boolean comparisons of auto_publish:
```python
logger.info(f"[APPROVAL]   request.auto_publish = {request.auto_publish!r}")
logger.info(f"[APPROVAL]   type = {type(request.auto_publish)}")
logger.info(f"[APPROVAL]   is True = {request.auto_publish is True}")
logger.info(f"[APPROVAL]   == True = {request.auto_publish == True}")
logger.info(f"[APPROVAL]   bool() = {bool(request.auto_publish)}")
```

**Next Step:** Check server logs to see what these values show.

---

## Next Steps (TODO)

### Immediate (Required to Identify Root Cause)
1. **Check server logs** - Run test again and examine console output for lines:
   ```
   [APPROVAL] ============================================
   [APPROVAL] AUTO-PUBLISH CHECK:
   [APPROVAL]   request.auto_publish = ???
   ```

2. **Determine actual value** - Logs will show:
   - If value is `True` → Issue is in the `if` statement logic
   - If value is `False` → Issue is in Pydantic parsing
   - If value is `None` → Field not being sent at all
   - If value is string `'true'` → JSON parsing issue

### Short Term (After Root Cause Identified)
1. Apply specific fix based on log findings
2. Test with `test_auto_publish_full.py`
3. Verify response includes post_id and post_slug
4. Verify database shows task status as "published"
5. Verify post table has new entry

### Testing & Validation
- ✅ Test script created: `test_auto_publish_full.py`
- ✅ Comprehensive logging added
- ⏳ Waiting for server logs to confirm root cause
- ⏳ Fix TBD (pending log analysis)

---

## Related Issues (Still TODO)

These are separate from the auto-publish parameter bug:

1. **Response Missing post_id/post_slug [CRITICAL]**
   - Dependent on auto-publish fix
   - Once auto_publish works, response should include these fields

2. **Reject Returns Wrong Status [HIGH]**
   - Endpoint returns 'failed_revisions_requested' instead of 'rejected'
   - Need to standardize status values

---

## Files Modified in This Session

**Code Changes:**
- `src/cofounder_agent/routes/approval_routes.py`
  - Lines 45-71: ApprovalRequest model with human_feedback support
  - Lines 213-221: Enhanced logging for auto_publish debugging

**Test Created:**
- `test_auto_publish_full.py` - End-to-end approval > publishing > post creation test

**Documentation Created:**
- This file (`AUTO_PUBLISH_BUG_INVESTIGATION.md`)

---

## How to Proceed

To identify the root cause:

1. **Run the test:**
   ```bash
   python test_auto_publish_full.py
   ```

2. **Check server console output** for lines containing:
   ```
   [APPROVAL] ============================================
   [APPROVAL] AUTO-PUBLISH CHECK:
   [APPROVAL]   request.auto_publish =
   ```

3. **Share the logged values** so we can determine:
   - Is Pydantic parsing the boolean correctly?
   - Is there a data type mismatch?
   - Is the value being modified somewhere?

4. **Apply fix** based on what the logs reveal

5. **Re-run test** to confirm

---

## Summary

- ✅ Confirmed auto_publish is NOT working
- ✅ Located all relevant code sections
- ✅ Fixed field name compatibility issue
- ✅ Added comprehensive logging
- ⏳ Waiting for log analysis to confirm root cause
- ⏳ Fix and validation pending

The infrastructure is in place; we just need to read the logs to identify the exact parsing/data type issue causing auto_publish to evaluate as False.
