# Bug Fix: UnboundLocalError in Content Approval Endpoint

## Issue

After approving content through the UI, the API returned a 500 error:

```
Error processing approval decision: cannot access local variable 'approval_timestamp_iso'
where it is not associated with a value
```

This is a Python `UnboundLocalError` - the variable was being referenced before it was defined.

## Root Cause

In [src/cofounder_agent/routes/content_routes.py](src/cofounder_agent/routes/content_routes.py), the approval endpoint had a code flow issue:

1. **Line 533**: Used `approval_timestamp_iso` in an early return statement
2. **Line 549**: Defined `approval_timestamp_iso` for the first time

When the early return path was taken (for already-approved tasks), the variable didn't exist yet.

## Solution

Moved the timestamp initialization to the beginning of the function logic, before any code path that might reference it:

```python
# ✅ Initialize timestamp variables early for use throughout the function
approval_timestamp = datetime.now()
approval_timestamp_iso = approval_timestamp.isoformat()
```

This ensures `approval_timestamp_iso` is always available, regardless of which code path is taken.

## Files Modified

- **[src/cofounder_agent/routes/content_routes.py](src/cofounder_agent/routes/content_routes.py)**
  - Moved timestamp initialization from line 549 to line 540
  - Reordered code to define variables before use
  - Added clarifying comment about early initialization

## Testing

✅ Backend restarted with fix applied  
✅ Health check endpoint responding  
✅ Variable now defined before any usage

## Result

Content approval workflow now completes successfully without UnboundLocalError exceptions.
