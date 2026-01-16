# Task Status System - Immediate Action Checklist

**Status:** 🔄 READY FOR IMPLEMENTATION  
**Created:** January 16, 2026  
**Priority:** 🔴 HIGH - User requirement: pending, in_progress, awaiting_approval, published

---

## 🚀 Quick Wins (1-2 Hours)

### [ ] 1. Add Missing Frontend Statuses

**File:** [web/oversight-hub/src/components/tasks/TaskList.jsx](web/oversight-hub/src/components/tasks/TaskList.jsx)

**Update `getStatusColor()` function:**

```javascript
// FIND: (around line 20)
const getStatusColor = (status) => {
  switch (status?.toLowerCase()) {
    case 'completed':
      return 'status-completed';
    case 'pending':
      return 'status-pending';
    case 'running':
      return 'status-running';
    case 'failed':
      return 'status-failed';
    default:
      return 'status-default';
  }
};

// REPLACE WITH:
const getStatusColor = (status) => {
  switch (status?.toLowerCase()) {
    case 'pending':
      return 'status-pending';
    case 'in_progress': // ✅ NEW
    case 'running': // Keep for backward compat
      return 'status-in-progress';
    case 'awaiting_approval': // ✅ NEW
      return 'status-awaiting-approval';
    case 'approved': // ✅ NEW (from screenshot)
      return 'status-approved';
    case 'completed':
      return 'status-completed';
    case 'published': // ✅ NEW
      return 'status-published';
    case 'failed':
      return 'status-failed';
    default:
      return 'status-default';
  }
};
```

**Update `getStatusIcon()` function:**

```javascript
// ADD after existing icons:
const getStatusIcon = (status) => {
  switch (status?.toLowerCase()) {
    case 'pending':
      return '⧗'; // Hourglass
    case 'in_progress':
    case 'running':
      return '⟳'; // Refresh
    case 'awaiting_approval': // ✅ NEW
      return '⚠'; // Warning
    case 'approved': // ✅ NEW
      return '✓'; // Check
    case 'completed':
      return '✓';
    case 'published':
      return '✓✓'; // Double check
    case 'failed':
      return '✗'; // X
    default:
      return '○';
  }
};
```

**Time:** 10 minutes

---

### [ ] 2. Add Missing CSS Color Definitions

**File:** [web/oversight-hub/src/routes/TaskManagement.css](web/oversight-hub/src/routes/TaskManagement.css)

**Add after line 514 (after .status-badge.status-failed):**

```css
/* ===== NEW STATUS COLORS ===== */

.status-badge.status-in-progress {
  background-color: rgba(33, 150, 243, 0.15);
  color: #2196f3;
  border: 1px solid #2196f3;
  animation: pulse 1.5s ease-in-out infinite;
}

.status-badge.status-awaiting-approval {
  background-color: rgba(255, 152, 0, 0.15);
  color: #ff9800;
  border: 1px solid #ff9800;
  animation: pulse-warning 1.5s ease-in-out infinite;
}

.status-badge.status-approved {
  background-color: rgba(156, 39, 176, 0.15);
  color: #9c27b0;
  border: 1px solid #9c27b0;
}

.status-badge.status-published {
  background-color: rgba(76, 175, 80, 0.15);
  color: #4caf50;
  border: 1px solid #4caf50;
}

/* Add table row styling */
.tasks-table tbody tr.status-in-progress {
  border-left: 4px solid #2196f3;
}

.tasks-table tbody tr.status-awaiting-approval {
  border-left: 4px solid #ff9800;
}

.tasks-table tbody tr.status-approved {
  border-left: 4px solid #9c27b0;
}

/* Warning pulse animation */
@keyframes pulse-warning {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}
```

**Time:** 10 minutes

---

### [ ] 3. Create TaskStatus Enum Module

**New File:** [src/cofounder_agent/utils/task_status.py](src/cofounder_agent/utils/task_status.py)

```python
"""Task status enumeration and validation utilities."""

from enum import Enum
from typing import Dict, Set, Optional


class TaskStatus(str, Enum):
    """Task lifecycle statuses."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# Valid status transitions
VALID_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.ON_HOLD,
        TaskStatus.CANCELLED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
    },
    TaskStatus.APPROVED: {
        TaskStatus.PUBLISHED,
        TaskStatus.ON_HOLD,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PUBLISHED: {TaskStatus.ON_HOLD},
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.ON_HOLD: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.REJECTED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: set(),  # Terminal state
}

# Terminal states (no further processing)
TERMINAL_STATUSES = {
    TaskStatus.PUBLISHED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


def is_valid_transition(
    current_status: TaskStatus,
    target_status: TaskStatus,
) -> bool:
    """Check if status transition is allowed."""
    if current_status == target_status:
        return True  # Allow updating to same status
    return target_status in VALID_TRANSITIONS.get(current_status, set())


def get_allowed_transitions(status: TaskStatus) -> Set[str]:
    """Get list of allowed status transitions for UI."""
    transitions = VALID_TRANSITIONS.get(status, set())
    return {s.value for s in transitions}


def is_terminal(status: TaskStatus) -> bool:
    """Check if status is terminal (no further transitions allowed)."""
    return status in TERMINAL_STATUSES


def validate_status(status_str: str) -> TaskStatus:
    """Validate and convert string to TaskStatus enum."""
    try:
        return TaskStatus(status_str.lower())
    except ValueError:
        valid_statuses = [s.value for s in TaskStatus]
        raise ValueError(f"Invalid status '{status_str}'. Must be one of: {', '.join(valid_statuses)}")
```

**Time:** 10 minutes

---

### [ ] 4. Update Content Router Service

**File:** [src/cofounder_agent/services/content_router_service.py](src/cofounder_agent/services/content_router_service.py)

**Update line 636 where it sets `approval_status`:**

```python
# FIND: (around line 636)
await database_service.update_task(
    task_id=task_id,
    updates={
        "status": "completed",
        "approval_status": "pending_human_review",  # ❌ OLD
        ...
    },
)

# REPLACE WITH:
await database_service.update_task(
    task_id=task_id,
    updates={
        "status": "awaiting_approval",  # ✅ NEW - Single status field
        ...
    },
)
```

**Time:** 5 minutes

---

### [ ] 5. Update Task Routes Comment

**File:** [src/cofounder_agent/routes/task_routes.py](src/cofounder_agent/routes/task_routes.py)

**Update line 485 documentation:**

```python
# FIND:
status: Optional[str] = Query(None, description="Filter by status (queued, pending, running, completed, failed)"),

# REPLACE WITH:
status: Optional[str] = Query(None, description="Filter by status (pending, in_progress, awaiting_approval, approved, published, failed, cancelled)"),
```

**Time:** 2 minutes

---

## ✅ Verification Checklist

After implementing above, verify:

- [ ] TaskList.jsx has all 9 status colors mapped
- [ ] CSS includes all new color definitions
- [ ] task_status.py created with enum
- [ ] Content router sets `awaiting_approval` not `approval_status`
- [ ] Task routes comment updated

**Test:**

```bash
# Frontend
npm run lint --workspace web/oversight-hub

# Backend
cd src/cofounder_agent && python -m pytest tests/unit/backend/test_task_status.py -v
```

---

## 📊 Before vs After

### Before (Current State)

```
Screenshot shows: pending, in_progress, completed, failed, approved
Backend returns:  pending, running, completed, failed
❌ Mismatch causing confusion
❌ No awaiting_approval
❌ No validation
```

### After (Your Requirements)

```
Frontend displays:  pending, in_progress, awaiting_approval, approved, published, failed
Backend returns:    pending, in_progress, awaiting_approval, approved, published, failed
✅ Consistent naming
✅ All user requirements met
✅ Transition validation ready
```

---

## 🔍 Testing Examples

After implementing, tasks should flow like:

```
Scenario 1: Content Generation (Happy Path)
pending → in_progress → awaiting_approval → approved → published

Scenario 2: Generation Fails
pending → in_progress → failed

Scenario 3: Approval Denied
pending → in_progress → awaiting_approval → rejected → in_progress (rework)

Scenario 4: User Pauses
in_progress → on_hold → in_progress
```

---

## 📝 What's NOT Changing Yet

The following are saved for Phase 2-3:

- ✋ Database ENUM constraint (requires migration)
- ✋ Status history audit table
- ✋ Transition validation endpoint
- ✋ Status change notifications

---

## Time Summary

| Task                       | Time       | Who      |
| -------------------------- | ---------- | -------- |
| Update TaskList.jsx        | 10 min     | Frontend |
| Update CSS                 | 10 min     | Frontend |
| Create task_status.py      | 10 min     | Backend  |
| Update content_router      | 5 min      | Backend  |
| Update task_routes comment | 2 min      | Backend  |
| **TOTAL**                  | **37 min** | Team     |

**Recommended:** Complete all 5 items in one session (< 1 hour).

---

## Next Steps After These Changes

1. ✅ Deploy updates to dev environment
2. ✅ Test task creation with new statuses
3. ✅ Verify frontend displays all colors correctly
4. ✅ Start Phase 2 (database migration) next week

---

**Prepared:** January 16, 2026  
**Status:** 🟢 Ready to implement  
**Estimated Completion:** Today (1 hour)
