# Oversight Hub Duplication Audit - January 16, 2026

## Executive Summary

Found **THREE CRITICAL DUPLICATION ISSUES** in the oversight hub that create redundant API calls, state confusion, and potential workflow conflicts.

---

## Issue #1: TaskActions vs ResultPreviewPanel (CRITICAL)

**Location:** [web/oversight-hub/src/components/tasks/TaskManagement.jsx](web/oversight-hub/src/components/tasks/TaskManagement.jsx#L355-L375)

**Problem:**
Two components are rendered simultaneously with overlapping handlers:

- **TaskActions** (line 357): Renders approval/reject/delete dialogs
- **ResultPreviewPanel** (line 369): Renders a modal with its own approval/reject/delete UI

**Impact:**

- User sees TWO different approval UIs for the same task
- State confusion about which component should handle the action
- Callbacks passed to both: `onApprove`, `onReject`, `onDelete`
- User might submit approval in ResultPreviewPanel, then see TaskActions dialog asking for the same thing

**Current Code:**

```jsx
{
  /* Task Actions Dialogs - Line 357 */
}
{
  selectedTask && (
    <TaskActions
      selectedTask={selectedTask}
      isLoading={false}
      onApprove={handleApprove} // ← DUPLICATE HANDLER
      onReject={handleReject} // ← DUPLICATE HANDLER
      onDelete={handleDeleteTask} // ← DUPLICATE HANDLER
      onClose={() => {
        setSelectedTask(null);
      }}
    />
  );
}

{
  /* Detail Panel - Line 369 */
}
{
  selectedTask && (
    <ResultPreviewPanel
      task={selectedTask}
      open={!!selectedTask}
      onClose={() => setSelectedTask(null)}
      onApprove={(feedback) => handleApprove(selectedTask.id, feedback)} // ← SAME HANDLER
      onReject={(reason) => handleReject(selectedTask.id, reason)} // ← SAME HANDLER
      onDelete={(task) => handleDeleteTask(task.id)} // ← SAME HANDLER
    />
  );
}
```

**Root Cause:**

- TaskActions was added to provide dialog-based actions
- ResultPreviewPanel already had modal-based approval/reject
- No decision made about which component is the primary handler
- Both left in place simultaneously

**Recommendation:**
Remove TaskActions or ResultPreviewPanel (one should be primary). They serve the same purpose with different UX patterns.

---

## Issue #2: Service Mismatch in TaskActions (CRITICAL)

**Location:** [web/oversight-hub/src/components/tasks/TaskActions.jsx](web/oversight-hub/src/components/tasks/TaskActions.jsx#L45-L100)

**Problem:**
TaskActions uses `unifiedStatusService` but TaskManagement passes legacy `onApprove`/`onReject` callbacks that use `taskService`:

**Service #1 - unifiedStatusService (used by TaskActions):**

```jsx
// TaskActions.jsx lines 65-70
const result = await unifiedStatusService.approve(selectedTask.id, feedback);
```

- Calls `/api/tasks/{id}/status/validated` (new endpoint)
- Falls back to `/api/orchestrator/executions/{id}` (legacy)
- Includes validation warnings and history tracking

**Service #2 - taskService (used by TaskManagement callbacks):**

```jsx
// TaskManagement.jsx line 150
await rejectTask(taskId, reason || ''); // From taskService
```

- Calls `/api/tasks/{id}/reject` (direct endpoint)
- No validation
- No history tracking

**The Conflict:**
If user submits via TaskActions:

1. unifiedStatusService.reject() called → `/api/tasks/{id}/status/validated` called
2. Then onReject callback triggered → taskService.rejectTask() called → `/api/tasks/{id}/reject` called
3. **TWO DIFFERENT ENDPOINTS** called for one rejection

**Impact:**

- Duplicate API calls for reject/approve through TaskActions
- Inconsistent state updates (unified service updates one way, legacy callback updates another)
- Backend receives conflicting data

**Recommendation:**
TaskActions should NOT pass callbacks to TaskManagement. TaskActions should handle its own API calls independently, then trigger a `onTaskUpdated` callback to refresh the task list.

---

## Issue #3: Service Method Duplication in taskService (MODERATE)

**Location:** [web/oversight-hub/src/services/taskService.js](web/oversight-hub/src/services/taskService.js#L155-L210)

**Problem:**
Multiple service methods call different endpoints for the same operations:

| Operation | Endpoint                           | Method | Service                                   |
| --------- | ---------------------------------- | ------ | ----------------------------------------- |
| Approve   | `/api/content/tasks/{id}/approve`  | POST   | ResultPreviewPanel direct call            |
| Approve   | `/api/tasks/{id}/status/validated` | PUT    | unifiedStatusService (TaskActions)        |
| Reject    | `/api/tasks/{id}/reject`           | POST   | taskService.rejectTask() (TaskManagement) |
| Reject    | `/api/tasks/{id}/status/validated` | PUT    | unifiedStatusService (TaskActions)        |
| Delete    | `/api/tasks/{id}`                  | DELETE | taskService.deleteTask()                  |

**Root Cause:**

- Three different approval endpoints exist:
  - Legacy: `/api/tasks/{id}/approve` (removed from ResultPreviewPanel)
  - Content-specific: `/api/content/tasks/{id}/approve` (ResultPreviewPanel)
  - Unified: `/api/tasks/{id}/status/validated` (unifiedStatusService)
- Each component uses a different endpoint without central routing

**Impact:**

- Confusion about which endpoint to call
- If backend changes one endpoint, multiple components fail
- No single source of truth for status operations
- Mixed response formats between endpoints

**Recommendation:**
Create a unified approval service that consolidates all three endpoints into one interface. All components call the same service.

---

## Summary Table: Current Duplication Flow

| Trigger                                   | Component          | Callback Used        | Service Called                          | Endpoint                           | Issue                                                                      |
| ----------------------------------------- | ------------------ | -------------------- | --------------------------------------- | ---------------------------------- | -------------------------------------------------------------------------- |
| User clicks Approve in TaskActions        | TaskActions        | handleApproveSubmit  | unifiedStatusService.approve()          | `/api/tasks/{id}/status/validated` | Then calls onApprove(id, feedback) → taskService.approveTask() → DUPLICATE |
| User clicks Approve in ResultPreviewPanel | ResultPreviewPanel | handleApprovalSubmit | Direct cofounderAgentClient.makeRequest | `/api/content/tasks/{id}/approve`  | ✓ Fixed - no callback chain                                                |
| TaskManagement gets onApprove callback    | TaskManagement     | handleApprove        | taskService.approveTask()               | `/api/tasks/{id}/approve`          | Legacy endpoint, bypassed by both UIs                                      |

---

## Recommended Resolution

### Option A: Remove TaskActions Component (Recommended)

- Delete TaskActions.jsx
- Keep ResultPreviewPanel as the single approval interface
- Pros: Simpler, user sees one UI
- Cons: Loses dialog-based flow

### Option B: Remove ResultPreviewPanel Approval

- Keep TaskActions as primary approval handler
- Remove approval/reject logic from ResultPreviewPanel
- ResultPreviewPanel only shows content preview
- Pros: Cleaner separation of concerns
- Cons: Less convenient for users

### Option C: Consolidate Both into Single Unified Component

- Create new ApprovalWorkflow component
- Combines best of both (preview + approval)
- No duplication
- Pros: Best UX
- Cons: Requires refactoring

### Option D (Quick Fix): Make TaskActions Non-Interactive

- Keep both components but disable TaskActions dialogs
- Use ResultPreviewPanel as sole approval handler
- Mark TaskActions for future removal
- Pros: Immediate fix, no breaking changes
- Cons: Technical debt

---

## Files Involved

### Components with Duplication

- [web/oversight-hub/src/components/tasks/TaskManagement.jsx](web/oversight-hub/src/components/tasks/TaskManagement.jsx) - Renders both TaskActions + ResultPreviewPanel
- [web/oversight-hub/src/components/tasks/TaskActions.jsx](web/oversight-hub/src/components/tasks/TaskActions.jsx) - Dialog-based approvals using unifiedStatusService
- [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) - Modal-based approvals using direct API calls

### Services with Duplication

- [web/oversight-hub/src/services/taskService.js](web/oversight-hub/src/services/taskService.js) - approveTask, rejectTask, deleteTask
- [web/oversight-hub/src/services/unifiedStatusService.js](web/oversight-hub/src/services/unifiedStatusService.js) - updateStatus, approve, reject

### Backend Endpoints Involved

- `/api/content/tasks/{id}/approve` - ResultPreviewPanel uses this
- `/api/tasks/{id}/status/validated` - unifiedStatusService uses this
- `/api/tasks/{id}/approve` - taskService.approveTask (legacy)
- `/api/tasks/{id}/reject` - taskService.rejectTask
- `/api/tasks/{id}` - taskService.deleteTask

---

## Next Steps

1. **Immediate (Quick Fix):**
   - Disable TaskActions from being rendered in TaskManagement.jsx
   - OR remove its onApprove/onReject callbacks to prevent double submission
   - Keep ResultPreviewPanel as sole approval handler

2. **Short-term (This Sprint):**
   - Consolidate to single approval endpoint on backend
   - Create unified approval service combining all three patterns
   - Update all components to use single service

3. **Medium-term (Next Sprint):**
   - Refactor to single ApprovalWorkflow component
   - Remove duplicate components entirely
   - Add integration tests to prevent regression

---

## Issue #4: Orphaned Callback Calls in ResultPreviewPanel (CRITICAL)

**Location:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx#L445-L1121)

**Problem:**

THREE callback invocations that trigger redundant parent handlers:

1. **Line 445** - Delete button:

```jsx
onClick={() => onDelete(task)}  // Calls TaskManagement.handleDeleteTask()
```

- Form doesn't make API call for delete
- Callback delegates to parent to handle deletion

2. **Line 1088** - Reject button (LOGIC ERROR):

```jsx
{['completed', 'approved'].includes(task.status) && (
  <button onClick={() => onReject(task)} ...>
    ✕ Reject
  </button>
)}
```

- Form doesn't make API call for reject (unlike approve)
- Callback delegates to parent: `TaskManagement.handleReject()` → `taskService.rejectTask()`
- **ISSUE:** Only allows reject on 'completed' or 'approved' tasks (not on 'awaiting_approval')
- Should instead have a reject form like approve has (lines 500-527)

3. **Line 1121** - Approve button (DUPLICATE CALLBACK):

```jsx
onClick={() => {
  const updatedTask = { /* ... */ };
  onApprove(updatedTask);  // Calls TaskManagement.handleApprove()
}}
```

- **CRITICAL:** The form already called `/api/content/tasks/{id}/approve` in handleApprovalSubmit (lines 500-527)
- Then clicking the button calls `onApprove()` which triggers TaskManagement.handleApprove()
- Which calls `taskService.approveTask()` → `/api/tasks/{id}/approve` (duplicate endpoint)
- **Result:** TWO API calls to different endpoints for single approval action

**Impact:**

- Reject only works on terminal states (completed/approved), not during normal workflow (awaiting_approval)
- Approve makes two API calls to two different endpoints
- Inconsistent error handling between approve and reject flows
- User submits form → sees success → then task list mysteriously updates again or shows errors

**Root Cause:**

- Original implementation had both form-based AND callback-based approval/reject
- Developer removed duplicate callback from approve form submit (good!) but left the button onclick callback (bad!)
- Reject never got a form (only callback) and has state restrictions

---

## Detailed Flow Comparison

### Current Broken Approve Flow

```text
User clicks "Approve" button (line 1121)
  ↓
Shows approval form modal (lines 850-1040)
  ↓
User fills feedback, reviewer ID, image
  ↓
User clicks "Submit Approval" button in form
  ↓
handleApprovalSubmit() (lines 458-527)
  ├─ Validates inputs (feedback, reviewer ID)
  ├─ Calls `/api/content/tasks/{id}/approve` API ✅
  ├─ Shows success alert
  └─ Closes modal
  ↓
BUT WAIT - there's ALSO line 1121's onClick:
  ├─ onClick={() => { onApprove(updatedTask) }}
  └─ This still fires when button is clicked (if not properly stopped)
  ↓
onApprove() callback (from TaskManagement)
  ├─ Calls TaskManagement.handleApprove()
  ├─ Which calls taskService.approveTask()
  └─ Makes DUPLICATE call to `/api/tasks/{id}/approve` ❌
```

### Current Broken Reject Flow

```text
Task status is 'completed' or 'approved'
  ↓
User clicks "Reject" button (line 1088)
  ↓
onReject(task) callback (no form, direct callback)
  ↓
TaskManagement.handleReject()
  ├─ Calls taskService.rejectTask()
  └─ Makes API call to `/api/tasks/{id}/reject` ✅ (but only works on 2 states)
```

### What SHOULD Happen

```text
User clicks "Approve" button
  ↓
Shows approval form modal
  ↓
User fills form and submits
  ↓
handleApprovalSubmit() makes API call to `/api/content/tasks/{id}/approve` ✅
  ↓
Form closes, modal closes
  ↓
TaskManagement refreshes task list (triggered by parent state update) ✅
  ✓ SINGLE API CALL
  ✓ NO DUPLICATE CALLBACKS
```

---

## Code Issues Inventory

| Issue                                                                  | Location                           | Type           | Severity | Status  |
| ---------------------------------------------------------------------- | ---------------------------------- | -------------- | -------- | ------- |
| TaskActions component rendered but dialogs never open                  | TaskManagement.jsx:357             | Dead Code      | MEDIUM   | Unfixed |
| TaskActions calls onApprove/onReject after API call                    | TaskActions.jsx:69-71              | Duplicate Call | CRITICAL | Unfixed |
| ResultPreviewPanel calls onDelete on delete button                     | ResultPreviewPanel.jsx:445         | Callback Issue | LOW      | Unfixed |
| ResultPreviewPanel calls onReject on reject button                     | ResultPreviewPanel.jsx:1088        | Logic Issue    | CRITICAL | Unfixed |
| ResultPreviewPanel calls onApprove after form submit                   | ResultPreviewPanel.jsx:1121        | Duplicate Call | CRITICAL | Unfixed |
| Reject only works on terminal states (completed/approved)              | ResultPreviewPanel.jsx:1084-1088   | Workflow Bug   | CRITICAL | Unfixed |
| Multiple approval endpoints not consolidated                           | taskService + unifiedStatusService | Architecture   | MEDIUM   | Unfixed |
| TaskActions uses unifiedStatusService; TaskManagement uses taskService | Service inconsistency              | Architecture   | MEDIUM   | Unfixed |

---

## Verification Checklist

- [ ] Identify which component should be primary (TaskActions or ResultPreviewPanel)
- [ ] Test approval flow to confirm no duplicate API calls
- [ ] Verify backend receives only one status update per action
- [ ] Check task list refreshes correctly after approval
- [ ] Confirm no console errors about duplicate state updates
- [ ] Test reject flow with same verification
- [ ] Test delete flow with same verification
- [ ] Verify reject works on 'awaiting_approval' status (currently blocked)
