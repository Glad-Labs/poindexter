# Task Status Lifecycle Audit Report

**Date:** January 16, 2026  
**Scope:** Comprehensive audit of task status system across frontend, backend, and database  
**Status:** ✅ AUDIT COMPLETE - Issues Found & Recommendations Provided

---

## Executive Summary

Your task status system has **functional basics but critical gaps in workflow semantics and database validation**:

- ✅ **Frontend:** Color coding is implemented (4 statuses)
- ✅ **Backend:** Status updates work and persist to PostgreSQL
- ⚠️ **Gaps:**
  - Only `pending`, `completed`, `failed`, and `in_progress` are fully supported
  - Missing required status: `awaiting_approval` (user requirement)
  - Status transitions not validated or constrained
  - No database ENUM constraint (accepts any string)
  - Inconsistent workflow stages vs task statuses
  - Screenshot shows `approved` status but not implemented in backend
  - No status transition audit trail
  - `running` status in code but showing as `in_progress` on frontend

---

## Part 1: Current Status Values Across System

### 1.1 Backend Status Values (Task Routes & Services)

**Where Statuses Are Set (in [src/cofounder_agent/routes/task_routes.py](src/cofounder_agent/routes/task_routes.py)):**

| Status      | Where Set                                                 | Semantics                       | Is Valid?              |
| ----------- | --------------------------------------------------------- | ------------------------------- | ---------------------- |
| `pending`   | All 18 task creation endpoints (line 224, 258, 278, etc.) | Initial state, waiting to start | ✅ Yes                 |
| `failed`    | Error handler (line 251)                                  | Task encountered error          | ✅ Yes                 |
| `running`   | API endpoint (line 630, update_data.status == "running")  | Task actively processing        | ⚠️ Inconsistent naming |
| `completed` | Content router service (line 635)                         | Task finished successfully      | ✅ Yes                 |
| (none)      | (none)                                                    | **MISSING:** Awaiting approval  | ❌ NOT IMPLEMENTED     |
| (none)      | (none)                                                    | **MISSING:** Approved/Published | ❌ NOT IMPLEMENTED     |

**Additional Statuses Found in Content Generation Pipeline** ([content_router_service.py](src/cofounder_agent/services/content_router_service.py)):

| Status                 | Where                               | Purpose                             | DB Stored?                     |
| ---------------------- | ----------------------------------- | ----------------------------------- | ------------------------------ |
| `pending`              | Initial task creation               | Task queued                         | ✅ Yes (content_tasks table)   |
| `generated`            | After content generation (line 404) | Content created, not approved       | ⚠️ Updated but not shown in UI |
| `completed`            | Pipeline finish (line 635)          | Entire pipeline done                | ✅ Yes                         |
| `pending_human_review` | Approval gate (line 636)            | **Separate field**, not main status | ⚠️ Different field!            |

**Status Filter Hint** (line 485 task_routes.py):

```python
status: Optional[str] = Query(None, description="Filter by status (queued, pending, running, completed, failed)"),
```

This comment shows older status names (`queued`) that may not be in use.

---

### 1.2 Frontend Status Display (Oversight Hub)

**Location:** [web/oversight-hub/src/components/tasks/TaskList.jsx](web/oversight-hub/src/components/tasks/TaskList.jsx)

**Supported Statuses in getStatusColor():**

```javascript
const getStatusColor = (status) => {
  switch (status?.toLowerCase()) {
    case 'completed':  → 'status-completed'   (✅ Green)
    case 'pending':    → 'status-pending'     (🟡 Yellow)
    case 'running':    → 'status-running'     (🔵 Blue)
    case 'failed':     → 'status-failed'      (🔴 Red)
    default:           → 'status-default'
  }
};
```

**Color Mapping** (from [TaskManagement.css](web/oversight-hub/src/routes/TaskManagement.css)):

| Frontend Status | CSS Class             | Color        | Hex     | Usage                        |
| --------------- | --------------------- | ------------ | ------- | ---------------------------- |
| `pending`       | `.status-pending`     | Yellow/Amber | #ffc107 | Task waiting                 |
| `running`       | `.status-running`     | Blue         | #2196f3 | In progress (animated pulse) |
| `completed`     | `.status-completed`   | Green        | #4caf50 | Done successfully            |
| `failed`        | `.status-failed`      | Red          | #f44336 | Error occurred               |
| `published`     | `.status-published`   | Purple       | #9c27b0 | **Defined but not mapped**   |
| `in-progress`   | `.status-in-progress` | Blue         | #2196f3 | **Different from `running`** |

**Issues Found:**

- ❌ `in-progress` is defined in CSS but NOT handled in getStatusColor() switch
- ❌ `published` is defined in CSS but NOT returned from any backend endpoint
- ⚠️ Both `running` and `in-progress` map to blue (#2196f3) → semantic confusion

---

### 1.3 Database Schema (PostgreSQL)

**Tasks Table Structure** (inferred from [tasks_db.py](src/cofounder_agent/services/tasks_db.py)):

```sql
CREATE TABLE content_tasks (
  id UUID PRIMARY KEY,
  status VARCHAR(50),           -- ❌ FREE TEXT - NO VALIDATION
  approval_status VARCHAR(50),  -- ❌ SEPARATE FIELD, SEPARATE CONCERN
  created_at TIMESTAMP,
  task_metadata JSONB,          -- Stores nested metadata (featured_image_url, etc.)
  -- ...other fields
);
```

**Problems:**

- ❌ `status` column is **VARCHAR(50) with NO constraints** - accepts any string
- ❌ `approval_status` field is **separate from main status** - creates two-layer workflow
- ✅ `task_metadata` JSONB allows flexible storage
- ❌ No CHECK constraint limiting to valid values
- ❌ No `status_updated_at` for audit trail

---

## Part 2: Screenshot Analysis (Your Current UI)

**What Screenshot Shows:**

You have 8 tasks with these statuses:

1. "approved" (gray badge) - **NOT RETURNED by backend**
2. "failed" (red badge) ✅
3. "completed" (green badge) ✅
4. "in_progress" (blue badge) - **Mismatch: backend uses "running"**

**Gap Analysis:**

- Frontend shows `approved` status, but there's no code setting it in backend
- Backend may be returning `in_progress` or `running` which frontend is displaying as `approved`
- This indicates **stale status value** or **mismatch in mapping**

---

## Part 3: Workflow Stage vs Status Confusion

**Current Problem:** Content pipeline has TWO separate workflows:

```
BACKEND WORKFLOW:
pending → generated → completed (pipeline stages)
                   ↓
              approval_status: pending_human_review

FRONTEND EXPECTATION (Your Requirement):
pending → in_progress → awaiting_approval → published
```

**The Issue:**

- Content router updates task status through stages: `pending` → `generated` → `completed`
- But `generated` is NOT handled by frontend (no color mapping)
- Pipeline then sets `approval_status` to `pending_human_review` (SEPARATE field)
- Frontend has no way to display "awaiting approval" because it's in a different field
- No status transition rules exist (e.g., you can manually set status to anything)

---

## Part 4: Recommended Task Status Lifecycle

Based on your minimum requirements + best practices:

### 4.1 Comprehensive Status Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    TASK LIFECYCLE                              │
└─────────────────────────────────────────────────────────────────┘

1. PENDING (🟡 Yellow)
   - Initial state when task created
   - Waiting for processing to start
   - Can transition to: IN_PROGRESS, CANCELLED

2. IN_PROGRESS (🔵 Blue)
   - Task actively being processed
   - Should have duration tracking (started_at)
   - Can transition to: AWAITING_APPROVAL, FAILED, COMPLETED

3. AWAITING_APPROVAL (🟠 Orange) *NEW*
   - Processing complete, human review needed
   - Can transition to: APPROVED, REJECTED, IN_PROGRESS (for rework)

4. APPROVED (🟣 Purple) *NEW*
   - Approved by human/system, ready for publishing
   - Can transition to: PUBLISHED, ON_HOLD

5. PUBLISHED (✅ Green) *NEW*
   - Task complete and live/published
   - Can transition to: ON_HOLD (if needed)

6. FAILED (🔴 Red)
   - Task encountered unrecoverable error
   - Can transition to: PENDING (retry), CANCELLED

7. ON_HOLD (⚪ Gray) *OPTIONAL*
   - Task paused, not abandoned
   - Can transition to: IN_PROGRESS, CANCELLED

8. REJECTED (🔴 Red) *OPTIONAL*
   - Approval was denied, needs rework
   - Can transition to: IN_PROGRESS (for rework)

9. CANCELLED (⚪ Gray) *OPTIONAL*
   - Task stopped, no further action
   - Terminal state - no transitions

TERMINAL STATES: PUBLISHED, FAILED (unrecoverable), CANCELLED
```

### 4.2 Your Minimum Requirements → Status Mapping

| Your Requirement  | Recommended Status  | Frontend Color   |
| ----------------- | ------------------- | ---------------- |
| pending           | `PENDING`           | Yellow (#ffc107) |
| in_progress       | `IN_PROGRESS`       | Blue (#2196f3)   |
| awaiting_approval | `AWAITING_APPROVAL` | Orange (#ff9800) |
| published         | `PUBLISHED`         | Green (#4caf50)  |
| (errors)          | `FAILED`            | Red (#f44336)    |

### 4.3 Valid Status Transitions (Validation Rules)

```python
VALID_TRANSITIONS = {
    "PENDING": ["IN_PROGRESS", "CANCELLED", "FAILED"],
    "IN_PROGRESS": ["AWAITING_APPROVAL", "FAILED", "ON_HOLD", "CANCELLED"],
    "AWAITING_APPROVAL": ["APPROVED", "REJECTED", "IN_PROGRESS", "CANCELLED"],
    "APPROVED": ["PUBLISHED", "ON_HOLD", "CANCELLED"],
    "PUBLISHED": ["ON_HOLD"],  # Terminal, except pause
    "FAILED": ["PENDING", "CANCELLED"],  # Retry or give up
    "ON_HOLD": ["IN_PROGRESS", "CANCELLED"],
    "REJECTED": ["IN_PROGRESS", "CANCELLED"],
    "CANCELLED": [],  # Terminal
}

# Validation logic:
def can_transition(current_status, target_status):
    return target_status in VALID_TRANSITIONS.get(current_status, [])
```

---

## Part 5: Database Schema Improvements

### 5.1 Recommended Schema Changes

```sql
-- Add ENUM type for type-safety
CREATE TYPE task_status_enum AS ENUM (
    'PENDING',
    'IN_PROGRESS',
    'AWAITING_APPROVAL',
    'APPROVED',
    'PUBLISHED',
    'FAILED',
    'ON_HOLD',
    'REJECTED',
    'CANCELLED'
);

-- Updated tasks table
CREATE TABLE content_tasks (
    id UUID PRIMARY KEY,

    -- Main workflow status (replaces separate approval_status field)
    status task_status_enum DEFAULT 'PENDING' NOT NULL,

    -- Audit trail
    status_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status_updated_by VARCHAR(255),  -- User who changed status

    -- Workflow dates
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,      -- When IN_PROGRESS began
    completed_at TIMESTAMP,    -- When task finished (any terminal state)

    -- Metadata (approval notes, rejection reason, etc.)
    task_metadata JSONB DEFAULT '{}',

    -- Other fields...
    topic VARCHAR(500),
    task_name VARCHAR(255),
    -- ...

    -- Constraints
    CONSTRAINT valid_completion_state CHECK (
        (completed_at IS NULL AND status NOT IN ('PUBLISHED', 'FAILED', 'CANCELLED'))
        OR
        (completed_at IS NOT NULL AND status IN ('PUBLISHED', 'FAILED', 'CANCELLED'))
    )
);

-- Audit table for status history
CREATE TABLE task_status_history (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES content_tasks(id),
    old_status task_status_enum,
    new_status task_status_enum NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by VARCHAR(255),
    reason VARCHAR(500),
    metadata JSONB
);
```

### 5.2 Migration Strategy

```python
# Step 1: Add new ENUM type and columns (non-breaking)
ALTER TABLE content_tasks
ADD COLUMN status_enum task_status_enum;

# Step 2: Migrate existing data
UPDATE content_tasks
SET status_enum = status::task_status_enum
WHERE status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED');

# Step 3: Handle legacy statuses
-- Map: 'pending' → PENDING, 'completed' → PUBLISHED, etc.
UPDATE content_tasks
SET status_enum = 'PENDING' WHERE status = 'pending';

UPDATE content_tasks
SET status_enum = 'PUBLISHED' WHERE status IN ('completed', 'generated');

UPDATE content_tasks
SET status_enum = 'FAILED' WHERE status = 'failed';

# Step 4: Remove old status column once verified
ALTER TABLE content_tasks DROP COLUMN status;

# Step 5: Rename status_enum to status
ALTER TABLE content_tasks RENAME COLUMN status_enum TO status;
```

---

## Part 6: Frontend Implementation Updates

### 6.1 Update Status Color Mapping

**File:** [web/oversight-hub/src/components/tasks/TaskList.jsx](web/oversight-hub/src/components/tasks/TaskList.jsx)

```javascript
const getStatusColor = (status) => {
  const statusMap = {
    pending: 'status-pending', // Yellow
    in_progress: 'status-in-progress', // Blue
    awaiting_approval: 'status-awaiting-approval', // Orange
    approved: 'status-approved', // Purple
    published: 'status-published', // Green
    failed: 'status-failed', // Red
    on_hold: 'status-on-hold', // Gray
    rejected: 'status-rejected', // Red-orange
    cancelled: 'status-cancelled', // Dark gray
  };

  return statusMap[status?.toLowerCase()] || 'status-default';
};

const getStatusIcon = (status) => {
  const iconMap = {
    pending: '⧗', // Hourglass
    in_progress: '⟳', // Refresh/spinning
    awaiting_approval: '⚠', // Warning/review
    approved: '✓', // Check
    published: '✓✓', // Double check
    failed: '✗', // X
    on_hold: '⊥', // Pause
    rejected: '✗', // X
    cancelled: '⊙', // Circle
  };

  return iconMap[status?.toLowerCase()] || '○';
};
```

### 6.2 Update CSS Styling

**File:** [web/oversight-hub/src/routes/TaskManagement.css](web/oversight-hub/src/routes/TaskManagement.css)

```css
/* Add to existing .status-badge styles */

.status-badge.status-awaiting-approval {
  background-color: rgba(255, 152, 0, 0.15);
  color: #ff9800;
  border: 1px solid #ff9800;
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

.status-badge.status-on-hold {
  background-color: rgba(158, 158, 158, 0.15);
  color: #9e9e9e;
  border: 1px solid #9e9e9e;
}

.status-badge.status-rejected {
  background-color: rgba(244, 67, 54, 0.15);
  color: #f44336;
  border: 1px solid #f44336;
}

.status-badge.status-cancelled {
  background-color: rgba(97, 97, 97, 0.15);
  color: #616161;
  border: 1px solid #616161;
  text-decoration: line-through;
}

/* Table row borders */
.tasks-table tbody tr.status-awaiting-approval {
  border-left: 4px solid #ff9800;
}

.tasks-table tbody tr.status-approved {
  border-left: 4px solid #9c27b0;
}

/* Add animation for awaiting-approval (similar to running) */
.status-badge.status-awaiting-approval {
  animation: pulse-orange 1.5s ease-in-out infinite;
}

@keyframes pulse-orange {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}
```

---

## Part 7: Backend Implementation Updates

### 7.1 Create Status Utilities Module

**New File:** [src/cofounder_agent/utils/task_status.py](src/cofounder_agent/utils/task_status.py)

```python
from enum import Enum
from typing import Set, Optional

class TaskStatus(str, Enum):
    """Task status enumeration with validation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

# Valid transitions mapping
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
    TaskStatus.CANCELLED: set(),  # Terminal
}

TERMINAL_STATUSES = {
    TaskStatus.PUBLISHED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

def is_valid_transition(
    current_status: TaskStatus,
    target_status: TaskStatus,
) -> bool:
    """Check if transition is allowed."""
    if current_status == target_status:
        return True  # Allow "updating" to same status
    return target_status in VALID_TRANSITIONS.get(current_status, set())

def get_allowed_transitions(status: TaskStatus) -> Set[str]:
    """Get list of allowed transitions for UI."""
    transitions = VALID_TRANSITIONS.get(status, set())
    return {s.value for s in transitions}

def is_terminal(status: TaskStatus) -> bool:
    """Check if status is terminal (no further transitions except pause)."""
    return status in TERMINAL_STATUSES
```

### 7.2 Update Task Routes with Validation

**File:** [src/cofounder_agent/routes/task_routes.py](src/cofounder_agent/routes/task_routes.py) - UPDATE

```python
from src.cofounder_agent.utils.task_status import (
    TaskStatus,
    is_valid_transition,
    is_terminal,
)

@router.put("/{task_id}/status", summary="Update task status with validation")
async def update_task_status(task_id: str, update_data: TaskStatusUpdateRequest):
    """Update task status with transition validation."""

    try:
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        current_status = TaskStatus(task.get("status", "pending"))
        target_status = TaskStatus(update_data.status)

        # Validate transition
        if not is_valid_transition(current_status, target_status):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_transition",
                    "current_status": current_status.value,
                    "target_status": target_status.value,
                    "allowed_transitions": [s.value for s in VALID_TRANSITIONS.get(current_status, set())],
                }
            )

        # Update status with audit trail
        await db_service.update_task(
            task_id,
            {
                "status": target_status.value,
                "status_updated_at": datetime.now(timezone.utc),
                "status_updated_by": update_data.updated_by or "system",
            }
        )

        # Record in audit table
        await db_service.log_status_change(
            task_id=task_id,
            old_status=current_status.value,
            new_status=target_status.value,
            reason=update_data.reason or None,
        )

        return {
            "task_id": task_id,
            "old_status": current_status.value,
            "new_status": target_status.value,
            "timestamp": datetime.now(timezone.utc),
        }

    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid status: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")
```

### 7.3 Update Content Router Service

**File:** [src/cofounder_agent/services/content_router_service.py](src/cofounder_agent/services/content_router_service.py) - UPDATE

```python
# Replace this pattern:
# "status": "pending" → status_generated (intermediate)
# "approval_status": "pending_human_review" (separate field)

# With this pattern:
await database_service.update_task(
    task_id=task_id,
    updates={
        "status": "awaiting_approval",  # ✅ Use main status field
        "status_updated_at": datetime.now(),
        "task_metadata": {
            # Store all generation metadata here
            "featured_image_url": result.get("featured_image_url"),
            "content": content_text,
            "seo_title": seo_title,
            # ... etc
        }
    },
)
```

---

## Part 8: Implementation Roadmap

### Phase 1: Foundation (Week 1)

- [ ] Create `task_status.py` utility module with `TaskStatus` enum and validation
- [ ] Add status color mappings to frontend components
- [ ] Update CSS with new colors (Orange for awaiting_approval, etc.)
- [ ] Update status comment in task_routes.py line 485

**Time:** 2-3 hours  
**Risk:** Low (UI only, no DB changes)

### Phase 2: Database Migration (Week 1-2)

- [ ] Create PostgreSQL ENUM type `task_status_enum`
- [ ] Add `status_updated_at`, `status_updated_by` columns
- [ ] Create `task_status_history` audit table
- [ ] Run migration (test in dev first)
- [ ] Update schema documentation

**Time:** 4-5 hours  
**Risk:** Medium (production data affected, needs backup & rollback plan)

### Phase 3: Backend Validation (Week 2)

- [ ] Add transition validation to `PUT /tasks/{id}/status` endpoint
- [ ] Update content router service to use new statuses
- [ ] Remove separate `approval_status` field (consolidate into main `status`)
- [ ] Update task creation to respect initial status
- [ ] Add audit logging for status changes

**Time:** 3-4 hours  
**Risk:** Medium (changes task update logic)

### Phase 4: Testing & Rollout (Week 2)

- [ ] Unit tests for status transitions
- [ ] Integration tests for workflow scenarios
- [ ] Update test fixtures
- [ ] Manual testing in staging
- [ ] Deploy to production with monitoring

**Time:** 2-3 hours  
**Risk:** Low (tests ensure safety)

---

## Part 9: Testing Strategy

### 9.1 Unit Tests for Status Validation

**File:** [tests/unit/backend/test_task_status.py](tests/unit/backend/test_task_status.py)

```python
import pytest
from src.cofounder_agent.utils.task_status import (
    TaskStatus,
    is_valid_transition,
    get_allowed_transitions,
)

def test_valid_transitions():
    """Test all valid transitions are allowed."""
    assert is_valid_transition(TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
    assert is_valid_transition(TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_APPROVAL)
    assert is_valid_transition(TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED)

def test_invalid_transitions():
    """Test invalid transitions are rejected."""
    assert not is_valid_transition(TaskStatus.PENDING, TaskStatus.PUBLISHED)
    assert not is_valid_transition(TaskStatus.FAILED, TaskStatus.IN_PROGRESS)
    assert not is_valid_transition(TaskStatus.PUBLISHED, TaskStatus.PENDING)

def test_terminal_states():
    """Test terminal states have no transitions."""
    published_transitions = get_allowed_transitions(TaskStatus.PUBLISHED)
    assert TaskStatus.ON_HOLD.value in published_transitions
    assert TaskStatus.IN_PROGRESS.value not in published_transitions

def test_complete_workflow():
    """Test happy path: pending → in_progress → awaiting → approved → published."""
    statuses = [
        TaskStatus.PENDING,
        TaskStatus.IN_PROGRESS,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.APPROVED,
        TaskStatus.PUBLISHED,
    ]

    for current, next_status in zip(statuses[:-1], statuses[1:]):
        assert is_valid_transition(current, next_status)
```

### 9.2 Integration Tests

```python
@pytest.mark.asyncio
async def test_task_status_workflow(db_service):
    """Test complete task lifecycle."""
    task_id = "test-task-123"

    # Create task (starts as PENDING)
    await db_service.add_task({
        "id": task_id,
        "topic": "Test Topic",
        "status": "pending",
    })

    task = await db_service.get_task(task_id)
    assert task["status"] == "pending"

    # Update to IN_PROGRESS
    await db_service.update_task(task_id, {"status": "in_progress"})
    task = await db_service.get_task(task_id)
    assert task["status"] == "in_progress"

    # Update to AWAITING_APPROVAL
    await db_service.update_task(task_id, {"status": "awaiting_approval"})
    task = await db_service.get_task(task_id)
    assert task["status"] == "awaiting_approval"

    # Cannot go back to PENDING
    with pytest.raises(InvalidTransitionError):
        await db_service.update_task(task_id, {"status": "pending"})
```

---

## Part 10: Current Issues & Quick Fixes

### Immediate Issues (Can Fix Today)

1. **Frontend shows `approved` but backend doesn't set it**
   - ✅ Fix: Add `approved` to `getStatusColor()` in TaskList.jsx
   - ✅ Or: Backend should return `awaiting_approval` → implement Part 3-7

2. **`in-progress` vs `running` mismatch**
   - ✅ Fix: Update backend to use `in_progress` consistently
   - ✅ Fix: Update frontend getStatusColor() to handle `in_progress`

3. **No `awaiting_approval` implementation**
   - ✅ Start: Create status enum (Part 7.1)
   - ✅ Frontend: Add color (orange)
   - ✅ Backend: Update content_router to set it

4. **`generated` status not visible in UI**
   - ✅ Option A: Map to yellow badge (intermediate state)
   - ✅ Option B: Remove and go straight to `awaiting_approval`

### Database-Level Issues (Requires Migration)

1. ❌ Status column is **free text** (no ENUM constraint)
2. ❌ No status transition audit trail
3. ❌ Separate `approval_status` field (should consolidate)
4. ❌ No `status_updated_at` column for tracking when status changed

---

## Part 11: Recommendations Summary

### High Priority (Do First)

| Item                                             | Effort    | Impact                         | Deadline  |
| ------------------------------------------------ | --------- | ------------------------------ | --------- |
| Add `awaiting_approval` status to frontend       | 30 min    | 🔴 Critical - User requirement | ASAP      |
| Fix `in_progress` vs `running` inconsistency     | 30 min    | 🔴 Critical - UI confusion     | ASAP      |
| Create TaskStatus enum (task_status.py)          | 1 hour    | 🟠 High - Foundation           | This week |
| Update content_router to set `awaiting_approval` | 1 hour    | 🟠 High - Business logic       | This week |
| Add transition validation to task_routes         | 1.5 hours | 🟠 High - Data integrity       | This week |

### Medium Priority (This Sprint)

| Item                                  | Effort    | Impact                   | Deadline |
| ------------------------------------- | --------- | ------------------------ | -------- |
| Create PostgreSQL ENUM type           | 1 hour    | 🟡 Medium - Safety       | Week 2   |
| Add `task_status_history` audit table | 1.5 hours | 🟡 Medium - Auditability | Week 2   |
| Update CSS for all status colors      | 1 hour    | 🟡 Medium - UX           | Week 2   |
| Write status transition tests         | 2 hours   | 🟡 Medium - Reliability  | Week 2   |

### Lower Priority (Future)

| Item                                            | Effort    | Impact                 | Notes                 |
| ----------------------------------------------- | --------- | ---------------------- | --------------------- |
| Add `on_hold`, `rejected`, `cancelled` statuses | 2 hours   | 🟢 Nice - Completeness | Optional enhancements |
| Create admin UI for status override             | 2 hours   | 🟢 Nice - Management   | Advanced feature      |
| Add status change notifications                 | 1.5 hours | 🟢 Nice - UX           | Future improvement    |

---

## Part 12: Color Palette Reference

**Current Implementation (from CSS):**

```
PENDING (Yellow)        #ffc107  - Task waiting to start
IN_PROGRESS (Blue)      #2196f3  - Task actively processing
COMPLETED (Green)       #4caf50  - Task finished successfully
FAILED (Red)            #f44336  - Task encountered error
PUBLISHED (Purple)      #9c27b0  - Content published

RECOMMENDED ADDITIONS:
AWAITING_APPROVAL (Orange)  #ff9800  - Needs human review
ON_HOLD (Gray)             #9e9e9e  - Paused
REJECTED (Red-Orange)      #ff5722  - Approval denied
CANCELLED (Dark Gray)      #616161  - Discontinued
```

**Semantic Color Mapping:**

- 🟢 Green = Success (completed, published)
- 🔴 Red = Error/Failure (failed, rejected)
- 🔵 Blue = In progress (processing)
- 🟡 Yellow = Waiting (pending)
- 🟠 Orange = Needs attention (awaiting approval)
- 🟣 Purple = Published/archived
- ⚪ Gray = Paused/cancelled

---

## Conclusion

Your task status system is **functional but incomplete**. You have:

- ✅ Basic frontend color coding
- ✅ Backend status setting
- ❌ No status transition validation
- ❌ Missing `awaiting_approval` status
- ❌ No database constraints
- ❌ Confusing workflow (separate `approval_status` field)

**Immediate action:** Implement Part 1 of the roadmap (add `awaiting_approval`, fix `in_progress` inconsistency) in the next 1-2 hours to match your requirements. Then proceed with phases 2-4 over the next 1-2 weeks.

---

## Quick Reference: Status Enum Implementation

```python
# src/cofounder_agent/utils/task_status.py
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
```

**Use it everywhere:**

```python
# Bad ❌
task["status"] = "pending"

# Good ✅
task["status"] = TaskStatus.PENDING.value
```

---

**Audit Prepared By:** GitHub Copilot  
**Date:** January 16, 2026  
**Next Review:** After Phase 1 implementation  
**Status:** Ready for Implementation
