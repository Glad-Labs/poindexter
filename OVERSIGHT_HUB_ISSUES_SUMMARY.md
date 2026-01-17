# Oversight Hub Duplication Issues - EXECUTIVE SUMMARY

## Critical Findings (4 Issues)

### 🔴 Issue #1: ResultPreviewPanel Line 1121 - Duplicate Approve Callback

**Severity:** CRITICAL  
**Impact:** Makes TWO API calls to different endpoints for a single approval

**What's happening:**

- Line 1121: User clicks "Approve" button
- Form handles approval and calls `/api/content/tasks/{id}/approve` ✓ (first call)
- Button's onClick ALSO calls `onApprove(updatedTask)` callback
- Callback triggers TaskManagement.handleApprove() → taskService.approveTask() → `/api/tasks/{id}/approve` ✗ (second call)

**Fix:**
Remove the `onApprove(updatedTask)` call from the button onClick handler. The form already handles the API call.

---

### 🔴 Issue #2: ResultPreviewPanel Line 1088 - Broken Reject Logic

**Severity:** CRITICAL  
**Impact:** Reject only works on 'completed'/'approved' tasks, NOT during normal workflow on 'awaiting_approval'

**What's happening:**

```jsx
{
  ['completed', 'approved'].includes(task.status) && (
    <button onClick={() => onReject(task)}>✕ Reject</button>
  );
}
```

Users cannot reject tasks that are in the workflow state they're meant to approve on ('awaiting_approval')

**Fix:**
Remove the status restriction. Allow reject on 'awaiting_approval' status at minimum. OR provide a proper reject form (like approve has) for all rejectable states.

---

### 🔴 Issue #3: TaskActions.jsx Lines 69-71 - Duplicate API Call After Service Call

**Severity:** CRITICAL  
**Impact:** If TaskActions dialogs were opened, would make TWO approval API calls

**What's happening:**

```jsx
const result = await unifiedStatusService.approve(selectedTask.id, feedback);
// ... then:
if (onApprove) {
  await onApprove(selectedTask.id, feedback); // DUPLICATE!
}
```

**Current impact:** None (dialogs are never opened)  
**Future impact:** High (if anyone enables these dialogs)

**Fix:**
Remove the `onApprove`/`onReject`/`onDelete` callback invocations. Let TaskActions handle its own API calls independently.

---

### 🔴 Issue #4: TaskManagement.jsx Lines 355-375 - Dead Component Code

**Severity:** MEDIUM  
**Impact:** TaskActions component is rendered but its dialogs are never opened; confuses future developers

**What's happening:**

- TaskActions component is rendered but dialogType is never set to 'approve'/'reject'/'delete'
- Dialogs always have `open={false}` and never appear
- Component consuming resources and adding UI complexity for zero functionality

**Why it happened:**

- Likely left from earlier refactoring when dialog-based workflow was considered
- ResultPreviewPanel (modal-based) became the chosen UX pattern
- Original TaskActions code never removed

**Fix:**
Either:

- Option A: Remove TaskActions component entirely (cleanest)
- Option B: Add code to actually open these dialogs from TaskTable/TaskManagement
- Option C: Comment it out with deprecation notice for future removal

---

## Secondary Issues (3 Issues)

### 🟡 Issue #5: Multiple Approval Endpoints (Architecture)

**Severity:** MEDIUM

Three different endpoints for approval:

- `/api/content/tasks/{id}/approve` (ResultPreviewPanel uses)
- `/api/tasks/{id}/status/validated` (unifiedStatusService uses)
- `/api/tasks/{id}/approve` (taskService uses - deprecated)

**Fix:** Backend should consolidate to single approval endpoint; frontend should route all approvals through unified service.

---

### 🟡 Issue #6: Service Inconsistency

**Severity:** MEDIUM

- TaskActions uses `unifiedStatusService`
- TaskManagement uses `taskService`
- ResultPreviewPanel uses direct API calls

No single source of truth for task operations.

**Fix:** Create unified approval/rejection service that all components use.

---

### 🟡 Issue #7: Delete Button Callback (Minor)

**Severity:** LOW

Line 445: `onDelete(task)` delegates to parent callback instead of handling directly. Inconsistent with rest of component architecture.

---

## Quick Fixes (Priority Order)

### 🚨 IMMEDIATE (Next 5 minutes)

1. **Remove Line 1121 callback**: Delete `onApprove(updatedTask)` call from approve button onClick
2. **Fix Line 1088 logic**: Remove status restriction OR add proper reject form

### ⚠️ SHORT-TERM (Next 30 minutes)

3. **Remove TaskActions component**: Comment out or delete lines 355-369 in TaskManagement.jsx
4. **Clean up TaskActions**: Remove onApprove/onReject/onDelete callback invocations if component is kept

### 📋 MEDIUM-TERM (This sprint)

5. Create unified approval service consolidating all three endpoints
6. Update all components to use single service
7. Add integration tests to prevent regression

---

## Files to Modify

**CRITICAL FIXES:**

- [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) - Lines 1088, 1121
- [web/oversight-hub/src/components/tasks/TaskManagement.jsx](web/oversight-hub/src/components/tasks/TaskManagement.jsx) - Lines 355-375

**SECONDARY CLEANUP:**

- [web/oversight-hub/src/components/tasks/TaskActions.jsx](web/oversight-hub/src/components/tasks/TaskActions.jsx) - Lines 69-71
- [web/oversight-hub/src/services/taskService.js](web/oversight-hub/src/services/taskService.js) - Review endpoints
- [web/oversight-hub/src/services/unifiedStatusService.js](web/oversight-hub/src/services/unifiedStatusService.js) - Review endpoints

---

## Testing After Fix

```bash
# Manual test scenarios

## Scenario 1: Approve Task
1. Open task in awaiting_approval status
2. Click Approve button
3. Fill form (feedback, reviewer ID)
4. Submit
✓ Should see ONE API call to `/api/content/tasks/{id}/approve`
✓ Modal closes
✓ Task list refreshes with updated status

## Scenario 2: Reject Task
1. Open task in awaiting_approval status
2. Click Reject button (should be visible)
3. [Form should appear if implemented, OR callback triggers directly]
✓ Should see ONE API call to `/api/tasks/{id}/reject`
✓ Task status updates to rejected
✓ Task list refreshes

## Scenario 3: Delete Task
1. Open task
2. Click Delete button
✓ Should see ONE API call to `/api/tasks/{id}`
✓ Task removed from list
```

---

## Document Location

Full detailed audit: [DUPLICATION_AUDIT_20250116.md](DUPLICATION_AUDIT_20250116.md)
