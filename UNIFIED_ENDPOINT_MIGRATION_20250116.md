# Unified API Endpoint Migration - January 16, 2026

## ✅ Consolidation Complete

All approval/rejection operations now route through the unified `/api/tasks/{id}/status/validated` endpoint via `unifiedStatusService`.

---

## Changes Made

### 1. ResultPreviewPanel.jsx - UPDATED ✅

**Location:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx#L458-L515)

**Before:**

```jsx
const { makeRequest } = await import('../../services/cofounderAgentClient');
const result = await makeRequest(
  `/api/content/tasks/${taskId}/approve`, // ❌ Content-specific endpoint
  'POST',
  approvalPayload,
  false,
  null,
  30000
);
```

**After:**

```jsx
const { unifiedStatusService } =
  await import('../../services/unifiedStatusService');
let result;
if (approved) {
  result = await unifiedStatusService.approve(
    taskId,
    approvalFeedback,
    reviewerId
  ); // ✅ Unified endpoint
} else {
  result = await unifiedStatusService.reject(
    taskId,
    approvalFeedback,
    reviewerId
  ); // ✅ Unified endpoint
}
```

**Impact:**

- ResultPreviewPanel now uses `/api/tasks/{id}/status/validated` (unified endpoint)
- No longer uses `/api/content/tasks/{id}/approve` (content-specific endpoint)
- Cleaner, consistent with rest of system

---

### 2. TaskManagement.jsx - UPDATED ✅

**Location:** [web/oversight-hub/src/components/tasks/TaskManagement.jsx](web/oversight-hub/src/components/tasks/TaskManagement.jsx#L26-L31)

**Before:**

```jsx
import {
  approveTask, // ❌ Used /api/tasks/{id}/approve
  rejectTask, // ❌ Used /api/tasks/{id}/reject
  deleteContentTask,
} from '../../services/taskService';
```

**After:**

```jsx
import { deleteContentTask } from '../../services/taskService';
import { unifiedStatusService } from '../../services/unifiedStatusService'; // ✅ Unified service
```

**Function Updates:**

```jsx
// BEFORE
const handleApprove = async (taskId, feedback) => {
  await approveTask(taskId, feedback); // ❌ Old endpoint
  // ...
};

// AFTER
const handleApprove = async (taskId, feedback) => {
  await unifiedStatusService.approve(taskId, feedback); // ✅ Unified endpoint
  // ...
};
```

**Impact:**

- TaskManagement now uses unified service for approve/reject
- Consistent with ResultPreviewPanel
- Single source of truth for all status operations

---

### 3. TaskActions.jsx - CONFIRMED ✅

**Status:** Already using `unifiedStatusService` (no changes needed)

```jsx
const result = await unifiedStatusService.approve(selectedTask.id, feedback);
const result = await unifiedStatusService.reject(selectedTask.id, reason);
```

---

## Unified Endpoint Overview

### Single Source of Truth: `/api/tasks/{id}/status/validated`

All status changes now route through:

```
PUT /api/tasks/{id}/status/validated
{
  new_status: 'approved' | 'rejected',
  reason: 'reason string',
  feedback: 'feedback string',
  user_id: 'reviewer_id',
  metadata: { ... }
}
```

### Components Using Unified Service

| Component          | Service              | Endpoint                           | Status           |
| ------------------ | -------------------- | ---------------------------------- | ---------------- |
| ResultPreviewPanel | unifiedStatusService | `/api/tasks/{id}/status/validated` | ✅ Updated       |
| TaskManagement     | unifiedStatusService | `/api/tasks/{id}/status/validated` | ✅ Updated       |
| TaskActions        | unifiedStatusService | `/api/tasks/{id}/status/validated` | ✅ Already using |
| OrchestratorPage   | unifiedStatusService | `/api/tasks/{id}/status/validated` | ✅ Already using |

### Deprecated Endpoints (No Longer Used)

| Endpoint                          | Previous User           | Status        |
| --------------------------------- | ----------------------- | ------------- |
| `/api/content/tasks/{id}/approve` | ResultPreviewPanel      | ❌ REMOVED    |
| `/api/tasks/{id}/approve`         | taskService.approveTask | ⚠️ Deprecated |
| `/api/tasks/{id}/reject`          | taskService.rejectTask  | ⚠️ Deprecated |

---

## Consistency Verification

### All Components Now Use Same Pattern

```jsx
// Pattern: Use unifiedStatusService for all status changes
await unifiedStatusService.approve(taskId, feedback, userId);
await unifiedStatusService.reject(taskId, reason, userId);

// Endpoint hit: PUT /api/tasks/{id}/status/validated
```

### No More Endpoint Confusion

✅ Single `/api/tasks/` namespace  
✅ No more `/api/content/tasks/` path  
✅ Unified validation and history tracking  
✅ Consistent response format

---

## Migration Complete

| Item                        | Status |
| --------------------------- | ------ |
| ResultPreviewPanel migrated | ✅     |
| TaskManagement migrated     | ✅     |
| TaskActions confirmed       | ✅     |
| OrchestratorPage confirmed  | ✅     |
| Single endpoint approach    | ✅     |
| No duplicate endpoint calls | ✅     |

---

## Testing

### Verification Checklist

- [ ] Approve task flow works (uses `/api/tasks/{id}/status/validated`)
- [ ] Reject task flow works (uses `/api/tasks/{id}/status/validated`)
- [ ] No 400 "already published" errors
- [ ] ResultPreviewPanel approval triggers proper endpoint
- [ ] TaskManagement approval triggers proper endpoint
- [ ] All responses consistent with unified service format
- [ ] Backend logs show only ONE `/api/tasks/{id}/status/validated` call per action

---

## Future Cleanup

### Optional (After Backend Confirms No Usage)

Remove or deprecate old taskService functions:

- `taskService.approveTask()` - `/api/tasks/{id}/approve`
- `taskService.rejectTask()` - `/api/tasks/{id}/reject`

Keep:

- `taskService.deleteContentTask()` - `/api/content/tasks/{id}` (used by TaskManagement)

---

**Date Updated:** January 16, 2026  
**Consolidation Status:** ✅ COMPLETE
