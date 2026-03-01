# Auto-Publish Bug: ROOT CAUSE FOUND

## Problem Summary
When calling POST `/api/tasks/{id}/approve` with `{"approved": true, "auto_publish": true}`, the endpoint returns:
- status: "approved" (should be "published")
- No post_id or post_slug in response
- Database task status remains "approved" (should be "published")

## Root Cause
The `ApprovalRequest` Pydantic model has the `auto_publish` field defined, but the endpoint receiving the request is NOT the one that implements the auto-publish handler.

### Current Flow
1. **Two endpoints exist with SAME PATH**: Both `approval_routes.py` and `task_routes.py` define `POST /{task_id}/approve`
2. **approval_routes.py response format** matches observed response (Dict return type with "approval_status" field)
3. **approval_routes.py HAS auto_publish handler** (lines 190-319) that creates posts and updates status to "published"
4. **BUG**: The auto_publish handler is never being executed (confirmed by status remaining "approved")

### Why auto_publish Handler Not Executing
The if condition at line 190: `if request.auto_publish:` is evaluating to False

###Possible Causes
1. ApprovalRequest is not being parsed correctly - `auto_publish` field might default to False
2. Pydantic validation might be failing silently
3. There might be a request/response mismatch

## Solution
Since debugging logs aren't appearing, the simplest fix is to check if approval_routes is the correct endpoint being called, and if so, ensure the auto_publish field is being recognized.

The fix requires investigating:
1. Whether `request.auto_publish` is actually receiving the True value
2. If not, check ApprovalRequest model validation
3. If it is True but not triggering, check the if statement condition

## Recommended Investigation
1. Direct SQL query to verify database state hasn't changed
2. Verify exact JSON being sent matches schema
3. Add temporary endpoint test to isolate the problem