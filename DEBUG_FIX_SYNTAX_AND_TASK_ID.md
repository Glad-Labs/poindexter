# Bug Fixes: Syntax Error & Task ID Validation

**Date:** January 15, 2026  
**Status:** ✅ FIXED  
**Severity:** Critical (blocks task processing and approval)

---

## Issue 1: Python Syntax Error in constraint_utils.py

### Error Message

```
ERROR:services.unified_orchestrator:[...] Error: invalid syntax (constraint_utils.py, line 286)
...
return 0{phase: _get_default_phase_target(phase, total_word_count) for phase in phase_names}
       ^
SyntaxError: invalid syntax
```

### Root Cause

Typo in `constraint_utils.py` line 286: `return 0{...}` instead of `return {...}`

The line was attempting to return a dictionary comprehension but had a stray `0` before the brace, which is invalid Python syntax.

### Fix Applied

**File:** `src/cofounder_agent/utils/constraint_utils.py` (lines 280-310)

**Before:**

```python
def _get_default_phase_target(phase: str, total_word_count: int) -> int:
    """Get default word count target for a specific phase."""
    if phase == "creative":
        return total_word_count
    elif phase == "qa":
        return int(total_word_count * 0.15)
    else:
        return 0{phase: _get_default_phase_target(phase, total_word_count) for phase in phase_names}  # ❌ SYNTAX ERROR

    return targets


def _get_default_phase_target(phase: str, total_word_count: int) -> int:  # ❌ DUPLICATE FUNCTION
    """Get default word count target for a specific phase."""
    if phase == "creative":
        return total_word_count
    elif phase == "qa":
        return int(total_word_count * 0.15)
    else:
        return 0
```

**After:**

```python
def _get_default_phase_target(phase: str, total_word_count: int) -> int:
    """Get default word count target for a specific phase."""
    if phase == "creative":
        return total_word_count
    elif phase == "qa":
        return int(total_word_count * 0.15)
    else:
        return 0  # ✅ CORRECT - returns 0 for non-measurable phases
```

**Changes:**

- ✅ Fixed typo: `return 0{...}` → `return 0`
- ✅ Removed duplicate function definition
- ✅ Removed orphaned `return targets` statement

---

## Issue 2: Task ID Validation Rejecting Legacy Numeric IDs

### Error Message

```
WARNING:utils.exception_handlers:HTTP Error 400: Invalid task ID format
INFO:     127.0.0.1:59028 - "POST /api/tasks/68/approve HTTP/1.1" 400 Bad Request
```

### Root Cause

The `/api/tasks/{task_id}/approve` and `/api/tasks/{task_id}/publish` endpoints were strictly validating that `task_id` must be a valid UUID using `UUID(task_id)`. However:

1. **Existing tasks in database use numeric IDs** (legacy from earlier development)
2. **React frontend receives numeric IDs** from the API response and tries to approve them
3. **API validation rejects numeric IDs** with "Invalid task ID format"

### Fix Applied

**File:** `src/cofounder_agent/routes/task_routes.py`

**Endpoint 1:** `POST /api/tasks/{task_id}/approve` (lines 1003-1015)

**Before:**

```python
# Validate UUID format
try:
    UUID(task_id)
except ValueError:
    raise HTTPException(status_code=400, detail="Invalid task ID format")  # ❌ REJECTS NUMERIC IDs
```

**After:**

```python
# Accept both UUID and numeric task IDs (backwards compatibility)
# Try to convert numeric ID to UUID if it's a string number
try:
    UUID(task_id)
except ValueError:
    # If not a valid UUID, check if it's a numeric ID (legacy tasks)
    if task_id.isdigit():
        # Convert numeric ID to string (it will work with get_task as-is)
        pass
    else:
        raise HTTPException(status_code=400, detail="Invalid task ID format")  # ✅ ALLOWS NUMERIC IDs
```

**Endpoint 2:** `POST /api/tasks/{task_id}/publish` (lines 1081-1087)

Applied same logic for backwards compatibility.

### Impact

- ✅ Numeric IDs (e.g., "68") are now accepted
- ✅ UUID format (e.g., "550e8400-e29b-41d4-a716-446655440000") still works
- ✅ Backwards compatible with legacy tasks in database
- ✅ Frontend can now approve tasks without ID format errors

---

## Browser Console Errors (Context Only)

The logs also show React/Chrome extension errors which are separate issues:

```javascript
TypeError: Cannot read properties of undefined (reading 'messenger')
// Chrome extension compatibility issue - not related to our backend code
```

```javascript
Error: Cannot respond. No request with id 9f30f0a0-8629-40ad-b70e-a036c924d6ff
// Request/response mismatch in messaging - can be addressed separately
```

These don't block functionality and appear to be extension-related.

---

## Testing the Fixes

### 1. Verify Python Syntax is Fixed

```bash
# Should import without syntax errors
cd src/cofounder_agent
python3 -c "from utils.constraint_utils import _get_default_phase_target; print('✅ Import successful')"
```

### 2. Test Constraint Function

```python
from utils.constraint_utils import _get_default_phase_target

# Should return:
# - 2000 for creative phase
# - 300 for qa phase (15% of 2000)
# - 0 for other phases
assert _get_default_phase_target("creative", 2000) == 2000
assert _get_default_phase_target("qa", 2000) == 300
assert _get_default_phase_target("research", 2000) == 0
print("✅ All constraint tests pass")
```

### 3. Test Numeric Task Approval

```bash
# Get a task (will have numeric ID)
curl -X GET http://localhost:8000/api/tasks?offset=0&limit=1 \
  -H "Authorization: Bearer YOUR_TOKEN"

# Response will include task with numeric ID like:
# {"id": "68", "task_id": "550e8400-...", "status": "completed"}

# Try to approve using numeric ID (should now work)
curl -X POST http://localhost:8000/api/tasks/68/approve \
  -H "Authorization: Bearer YOUR_TOKEN"

# Expected: 200 OK (not 400 Bad Request)
```

### 4. Test UUID Task Approval (Still Works)

```bash
# Create a new task (gets UUID)
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"task_type": "blog_post", "topic": "Test"}'

# Response will have UUID task_id

# Approve using UUID (should continue to work)
curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/approve \
  -H "Authorization: Bearer YOUR_TOKEN"

# Expected: 200 OK
```

---

## Deployment Notes

- **No database migration required** - code-only changes
- **Backwards compatible** - legacy numeric IDs still work
- **No data cleanup needed** - old tasks don't need modification
- **Safe to deploy** - changes are additive (more permissive, not less)
- **Immediate effects** when code is reloaded:
  - Python syntax errors eliminated
  - Task approval endpoint accepts numeric IDs
  - Existing task approvals in UI should now work

---

## Summary

| Issue                               | Type   | Severity | Status   |
| ----------------------------------- | ------ | -------- | -------- |
| Syntax error in constraint_utils.py | Python | Critical | ✅ FIXED |
| Task ID validation too strict       | Logic  | Critical | ✅ FIXED |
| Duplicate function definition       | Code   | High     | ✅ FIXED |

**Result:** Task creation and approval workflows should now complete without errors.
