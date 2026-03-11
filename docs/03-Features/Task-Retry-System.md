# Task Retry System & Enhanced Visibility

**Feature Status:** ✅ Production-ready
**Version:** 3.0.39
**Completion Date:** March 10, 2026
**Phase:** 2C - Task Management UI Enhancements

---

## Overview

The Task Retry System provides a complete solution for retrying failed tasks with full audit trails, real-time status visibility, and intelligent queue management. This system integrates with the existing status change service to ensure validated transitions and metadata persistence.

---

## Features

### 1. **Validated Retry Flow**

Task retry functionality routes through the enhanced status change service, ensuring:

- ✅ Validated status transitions (failed → pending)
- ✅ Audit trail logging for every retry attempt
- ✅ Metadata persistence across retry cycles
- ✅ Human approval integration when required

**API Endpoint:**

```
POST /api/tasks/{task_id}/status/validated
Body: {
  "new_status": "pending",
  "action": "retry",
  "changed_by": "user@example.com"
}
```

### 2. **Retry Counter & Badge System**

Visual indicators show retry attempts throughout the UI:

**Task List View:**

- Retry badge displays next to status badge
- Format: "Retry #3" in cyan with border
- Tooltip shows full count on hover

**Task Detail Modal:**

- Retry badge in dialog title header
- Persists across tab navigation
- Updates in real-time via WebSocket

**Metadata Fields:**

- `retry_count` - Total number of retry attempts
- `last_retry_at` - Timestamp of most recent retry
- `last_retry_by` - User who initiated retry

### 3. **Step-Aware Status Display**

Real-time visibility into task execution stages:

**Status Badge with Step Label:**

```
┌─────────────────────┐
│ In Progress         │  ← Status badge
│ Generating content  │  ← Current step/stage
└─────────────────────┘
```

**Backend Stage Updates:**
The task executor writes stage progression to `task_metadata`:

| Stage                | Percentage | Message                       |
| -------------------- | ---------- | ----------------------------- |
| `queued`             | 5%         | "Task queued for processing"  |
| `content_generation` | 20%        | "Generating content"          |
| `finalizing`         | 90%        | "Finalizing task output"      |
| `complete`           | 100%       | "Task completed successfully" |

**Frontend Extraction:**

```javascript
const metadata = getTaskMetadata(task);
const currentStep = metadata.message || metadata.stage;
```

### 4. **Enhanced Progress Visualization**

#### Stage-Based Color Coding

Progress bars change color based on execution stage:

- 🟠 **Orange (Queued):** Task waiting in queue (0-20%)
- 🔵 **Blue (Generating):** Content generation active (20-80%)
- 🟢 **Green (Finalizing):** Final processing (80-100%)
- 🔷 **Cyan (Default):** Other stages

#### Animated Indicators

Active tasks display visual feedback:

- Shimmer animation on progress bars
- Glowing shadow effects
- Smooth transitions with cubic-bezier easing
- Pulsing indicator dot on Timeline tab

#### Progress Bar in Modal Header

Detail modal shows real-time execution progress:

```
Task Details: Blog Post Title          Retry #2
─────────────────────────────────────────────────
Generating content                           35%
████████████░░░░░░░░░░░░░░░░░░░░░░░░
```

### 5. **Queue Mechanics Fix**

**Problem:** Resume action set tasks to `in_progress`, but executor only polls `pending` tasks.

**Solution:** Changed resume action to set status to `pending`:

```python
# bulk_task_routes.py
status_map = {
    "pause": "on_hold",
    "resume": "pending",  # Changed from "in_progress"
    "cancel": "cancelled",
    "reject": "rejected"
}
```

**Impact:**

- ✅ Resumed tasks picked up by executor immediately
- ✅ Consistent with pending task queue pattern
- ✅ No manual intervention required

---

## Technical Architecture

### Backend Components

#### 1. Enhanced Status Change Service

**File:** `src/cofounder_agent/services/enhanced_status_change_service.py`

**Key Features:**

- Metadata merge (preserves existing fields)
- Retry counter increment on `action="retry"`
- Audit trail logging
- Status transition validation

**Retry Increment Logic:**

```python
if action == "retry" and existing_metadata:
    retry_count = existing_metadata.get("retry_count", 0) + 1
    merged_metadata = {
        **existing_metadata,
        **new_metadata,
        "retry_count": retry_count,
        "last_retry_at": datetime.utcnow().isoformat(),
        "last_retry_by": changed_by
    }
```

#### 2. Task Executor Stage Updates

**File:** `src/cofounder_agent/services/task_executor.py`

**Helper Function:**

```python
async def update_processing_stage(
    task_id: str,
    stage: str,
    message: str,
    percentage: int
):
    """Update task metadata with current processing stage"""
    metadata = {
        "stage": stage,
        "message": message,
        "percentage": percentage,
        "updated_at": datetime.utcnow().isoformat()
    }
    await db_service.tasks.update_task_metadata(task_id, metadata)
    await emit_task_progress(task_id, metadata)
```

**Stage Progression:**

```python
# Stage 1: Queued
await update_processing_stage(task_id, "queued", "Task queued for processing", 5)

# Stage 2: Content Generation
await update_processing_stage(task_id, "content_generation", "Generating content", 20)

# Stage 3: Finalizing
await update_processing_stage(task_id, "finalizing", "Finalizing task output", 90)

# Stage 4: Complete
await update_processing_stage(task_id, "complete", "Task completed successfully", 100)
```

### Frontend Components

#### 1. Task Management Page

**File:** `web/oversight-hub/src/routes/TaskManagement.jsx`

**Key Helpers:**

```javascript
// Safe metadata extraction
const getTaskMetadata = (task) => {
  const metadata = task?.task_metadata;
  if (!metadata) return {};
  if (typeof metadata === 'object') return metadata;
  if (typeof metadata === 'string') {
    try {
      return JSON.parse(metadata);
    } catch {
      return {};
    }
  }
  return {};
};

// CSS class normalization
const getStatusClass = (status) =>
  String(status || 'unknown')
    .toLowerCase()
    .replace(/[_\s]+/g, '-');

// Step label extraction
const getTaskStepLabel = (task) => {
  const metadata = getTaskMetadata(task);
  const rawStep = metadata.message || metadata.stage;
  if (!rawStep) return '';

  const status = String(task?.status || '').toLowerCase();
  if (!['pending', 'in_progress', 'running'].includes(status)) {
    return '';
  }

  return formatStepLabel(rawStep);
};

// Retry count extraction
const getRetryCount = (task) => {
  const metadata = getTaskMetadata(task);
  return Number(metadata.retry_count || 0);
};
```

#### 2. Task Detail Modal

**File:** `web/oversight-hub/src/components/tasks/TaskDetailModal.jsx`

**Progress Header:**

```jsx
{
  isActiveTask && taskPercentage > 0 && (
    <Box sx={{ width: '100%' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
        <span>{taskMessage || taskStage || 'Processing...'}</span>
        <span>{taskPercentage}%</span>
      </Box>
      <Box
        sx={
          {
            /* Progress bar container */
          }
        }
      >
        <Box
          sx={{
            width: `${taskPercentage}%`,
            backgroundColor: '#00d9ff',
            boxShadow: '0 0 10px rgba(0, 217, 255, 0.5)',
          }}
        />
      </Box>
    </Box>
  );
}
```

**Timeline Tab Enhancement:**

```jsx
<TabPanel value={tabValue} index={1}>
  {isActiveTask && (taskStage || taskMessage) && (
    <Box sx={{ /* Current Execution Stage card */ }}>
      <h4>🔄 Current Execution Stage</h4>
      <span>{taskPercentage}% Complete</span>
      <div>{taskMessage || taskStage}</div>
    </Box>
  )}
  <StatusTimeline ... />
</TabPanel>
```

#### 3. CSS Styling

**File:** `web/oversight-hub/src/routes/TaskManagement.css`

**Progress Bar with Stage Colors:**

```css
.progress-fill {
  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 0 8px rgba(0, 217, 255, 0.4);
}

/* Stage-specific colors */
.progress-fill[data-stage='queued'] {
  background: linear-gradient(90deg, #ffa726, #ffb74d);
}

.progress-fill[data-stage='content_generation'] {
  background: linear-gradient(90deg, #42a5f5, #64b5f6);
}

.progress-fill[data-stage='finalizing'] {
  background: linear-gradient(90deg, #66bb6a, #81c784);
}

/* Animated shimmer */
.progress-fill.active::after {
  content: '';
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.3) 50%,
    transparent 100%
  );
  animation: shimmer 2s infinite;
}
```

---

## Usage Guide

### Retrying a Failed Task

**From Task List:**

1. Locate failed task (red status badge)
2. Click task row to open detail modal
3. Navigate to "Content & Approval" tab
4. Click "Retry" button in Task Control Panel
5. Confirm retry action
6. Task status changes to `pending`
7. Retry badge appears showing attempt count

**From Bulk Actions:**

1. Select multiple failed tasks (checkboxes)
2. Click "Resume" button in bulk actions toolbar
3. Confirm bulk retry
4. All selected tasks set to `pending`
5. Retry counters increment for each task

### Monitoring Task Progress

**Real-Time Updates:**

- Watch progress bar fill as task executes
- Color changes indicate stage transitions
- Step label shows current execution phase
- Percentage updates every few seconds

**Timeline Tab:**

- Shows "Current Execution Stage" card for active tasks
- Displays percentage complete badge
- Lists historical status transitions below

**WebSocket Integration:**

- Frontend auto-refreshes when task status changes
- Progress updates arrive via `/api/workflow-progress/{id}`
- No manual refresh needed

---

## API Reference

### Retry Task

```http
POST /api/tasks/{task_id}/status/validated
Content-Type: application/json

{
  "new_status": "pending",
  "action": "retry",
  "changed_by": "user@example.com",
  "metadata": {
    "retry_reason": "Content quality issues resolved"
  }
}
```

**Response:**

```json
{
  "success": true,
  "task_id": "uuid-here",
  "status": "pending",
  "metadata": {
    "retry_count": 2,
    "last_retry_at": "2026-03-08T10:30:00Z",
    "last_retry_by": "user@example.com",
    "retry_reason": "Content quality issues resolved"
  },
  "audit_trail": {
    "transition": "failed -> pending",
    "action": "retry",
    "timestamp": "2026-03-08T10:30:00Z"
  }
}
```

### Update Task Stage

```http
PATCH /api/tasks/{task_id}/metadata
Content-Type: application/json

{
  "stage": "content_generation",
  "message": "Generating content with AI",
  "percentage": 35
}
```

**Response:**

```json
{
  "success": true,
  "task_id": "uuid-here",
  "metadata": {
    "stage": "content_generation",
    "message": "Generating content with AI",
    "percentage": 35,
    "updated_at": "2026-03-08T10:31:15Z"
  }
}
```

---

## Configuration

### Enable/Disable Features

**Environment Variables:**

```env
# Retry system
ENABLE_TASK_RETRY=true
MAX_RETRY_ATTEMPTS=5
RETRY_BACKOFF_SECONDS=300

# Progress tracking
ENABLE_TASK_PROGRESS_TRACKING=true
PROGRESS_UPDATE_INTERVAL_MS=2000

# WebSocket updates
ENABLE_REALTIME_UPDATES=true
WEBSOCKET_RECONNECT_DELAY_MS=3000
```

### Retry Limits

Configure maximum retry attempts in `task_executor.py`:

```python
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "5"))

async def can_retry_task(task_id: str) -> bool:
    metadata = await get_task_metadata(task_id)
    retry_count = metadata.get("retry_count", 0)
    return retry_count < MAX_RETRY_ATTEMPTS
```

---

## Testing

### Manual Testing Checklist

**Retry Flow:**

- [ ] Retry failed task from detail modal
- [ ] Verify retry badge appears with count
- [ ] Confirm metadata persists retry info
- [ ] Check audit trail logs transition
- [ ] Validate task picked up by executor

**Progress Visualization:**

- [ ] Verify progress bar appears for active tasks
- [ ] Check color changes match stage
- [ ] Confirm shimmer animation on active bars
- [ ] Test progress updates in real-time

**Status Display:**

- [ ] Check step labels show for pending/in_progress
- [ ] Verify step labels hidden for completed/failed
- [ ] Confirm CSS classes normalized correctly
- [ ] Test status formatting (title case)

**Detail Modal:**

- [ ] Progress bar visible in header for active tasks
- [ ] Timeline tab shows Current Execution Stage card
- [ ] Pulsing indicator appears on Timeline tab
- [ ] All tabs render without errors

### Automated Tests

**Backend Tests:**

```bash
# Test retry metadata increment
pytest tests/unit/backend/services/test_enhanced_status_change.py::test_retry_increments_counter -v

# Test stage updates
pytest tests/unit/backend/services/test_task_executor.py::test_update_processing_stage -v

# Test queue pickup
pytest tests/integration/test_resume_to_pending.py -v
```

**Frontend Tests:**

```bash
# Run oversight hub tests
cd web/oversight-hub
npm test -- --testPathPattern=TaskManagement
npm test -- --testPathPattern=TaskDetailModal
```

---

## Troubleshooting

### Retry button not working

**Symptom:** Clicking retry does nothing or shows error

**Solutions:**

1. Check browser console for JS errors
2. Verify task status is `failed` or `rejected`
3. Confirm user has retry permissions
4. Check backend logs for validation errors

### Retry badge not appearing

**Symptom:** Task was retried but badge not visible

**Solutions:**

1. Verify `task_metadata.retry_count` field exists
2. Check task list refreshed after retry
3. Inspect metadata JSON in task detail
4. Clear browser cache and reload

### Progress bar not updating

**Symptom:** Progress stuck at 0% or not moving

**Solutions:**

1. Check WebSocket connection status
2. Verify backend is writing to `task_metadata.percentage`
3. Confirm task status is `pending` or `in_progress`
4. Check browser DevTools Network tab for WebSocket frames

### Step labels not showing

**Symptom:** Status badge displays but no step text

**Solutions:**

1. Verify `task_metadata.message` or `task_metadata.stage` populated
2. Check task status is pending/in_progress/running
3. Inspect CSS for `.status-step-text` visibility
4. Confirm metadata parsing in `getTaskStepLabel()`

### Task stuck in queue

**Symptom:** Resumed task stays `pending` indefinitely

**Solutions:**

1. Check executor is running: `curl http://localhost:8000/health`
2. Verify database connection: Check PostgreSQL logs
3. Query pending tasks: `SELECT * FROM content_tasks WHERE status='pending'`
4. Restart executor: `npm run dev:cofounder`

---

## Performance Considerations

### Database Impact

**Metadata Updates:**

- Each stage update writes to `task_metadata` JSON field
- Uses partial updates (JSONB merge in PostgreSQL)
- Minimal impact: ~5ms per update

**Retry Counter:**

- Single field increment on retry action
- No additional queries required
- Batched with status transition

### Frontend Optimization

**Progress Polling:**

- WebSocket preferred over polling
- Falls back to 30-second interval if WS unavailable
- Debounced metadata parsing (100ms)

**CSS Animations:**

- GPU-accelerated transforms only
- Shimmer effect uses `transform: translateX`
- No layout thrashing

---

## Future Enhancements

### Planned Features

- [ ] Retry scheduling with exponential backoff
- [ ] Bulk retry with filters (e.g., "retry all failed today")
- [ ] Retry history timeline (show all attempts)
- [ ] Configurable retry strategies per task type
- [ ] Email notifications on retry success/failure
- [ ] Retry analytics dashboard

### Extension Points

- Custom retry validators (`IRetryValidator` interface)
- Configurable stage definitions per workflow
- Plugin system for progress bar themes
- Webhook notifications on retry events

---

## References

- **Backend Service:** `src/cofounder_agent/services/enhanced_status_change_service.py`
- **Task Executor:** `src/cofounder_agent/services/task_executor.py`
- **Bulk Routes:** `src/cofounder_agent/routes/bulk_task_routes.py`
- **Frontend UI:** `web/oversight-hub/src/routes/TaskManagement.jsx`
- **Detail Modal:** `web/oversight-hub/src/components/tasks/TaskDetailModal.jsx`
- **Styling:** `web/oversight-hub/src/routes/TaskManagement.css`
- **Status Components:** `web/oversight-hub/src/components/tasks/StatusComponents.jsx`
- **Version History:** `VERSION_HISTORY.md` - Phase 2C section

---

**Last Updated:** March 8, 2026  
**Maintained By:** Glad Labs Development Team  
**Status:** Production | Actively Maintained
