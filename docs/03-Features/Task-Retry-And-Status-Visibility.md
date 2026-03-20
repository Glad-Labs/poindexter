<!-- markdownlint-disable MD022 MD031 MD032 MD034 MD040 MD060 -->

# Task Retry and Status Visibility System

**Feature Area:** Task Management  
**Components:** Oversight Hub (Frontend), Enhanced Status Change Service (Backend), Task Executor  
**Status:** ✅ Production Ready  
**Version:** 3.1.0  
**Implementation Date:** March 8, 2026

---

## Overview

Comprehensive task retry and status visibility system providing visual feedback for task execution, retry tracking, and real-time progress monitoring in the Oversight Hub dashboard.

### Key Capabilities

- **Validated Retry Flow** - Route retry actions through validated status endpoint with transition rules
- **Retry Tracking** - Persistent retry counter with badges in UI
- **Step-Aware Status Display** - Live execution stage visibility
- **Stage-Based Progress** - Color-coded progress bars based on execution phase
- **Real-Time Updates** - Backend writes stage progression during task execution
- **Timeline Visualization** - Enhanced detail modal with current execution status

---

## Architecture

### Frontend Components

#### TaskManagement.jsx

- Main task list with status badges, retry counters, and step labels
- Bulk actions routed through `unifiedStatusService`
- Status normalization (in_progress → in-progress for CSS)
- Metadata extraction helpers:
  - `getTaskMetadata()` - Safe JSON parsing
  - `getStatusClass()` - CSS class normalization
  - `formatStatusLabel()` - Human-friendly labels
  - `formatStepLabel()` - Step text formatting
  - `getTaskStepLabel()` - Extract current step from metadata
  - `getRetryCount()` - Extract retry attempts

#### TaskDetailModal.jsx

- Progress bar in dialog header with percentage and stage message
- Retry badge display in title
- Enhanced Timeline tab with "Current Execution Stage" card
- Pulsing indicator on Timeline tab for active tasks
- Live metadata display

#### TaskManagement.css

- Status-specific badge styling (9 states)
- Animated progress bars with shimmer effect
- Stage-based progress colors:
  - **Orange** - Queued (0-20%)
  - **Blue** - Content generation (20-80%)
  - **Green** - Finalizing/complete (80-100%)
  - **Cyan** - Default
- Flexbox layouts for status cells with step text
- Retry counter badge styling

### Backend Components

#### enhanced_status_change_service.py

- `validate_and_change_status()` - Enforces status transition rules
- Metadata merge logic preserves existing fields
- Retry counter increment when `action="retry"`:
  - `retry_count` - Incremented on each retry
  - `last_retry_at` - Timestamp of last retry
  - `last_retry_by` - User/system identifier
- Audit trail logging for all status changes

#### task_executor.py

- `update_processing_stage()` - Writes stage progression to task_metadata
- Stage flow:
  1. **queued** (5%) - Task accepted by executor
  2. **content_generation** (20%) - Active generation in progress
  3. **finalizing** (90%) - Post-processing and validation
  4. **complete** (100%) - Task finished successfully
- Real-time WebSocket emission via `emit_task_progress()`
- Final result includes stage, percentage, and message fields

#### bulk_task_routes.py

- Resume action sets `status="pending"` (not `in_progress`)
- Ensures executor polling picks up resumed tasks
- Consistent with queue mechanics (executor polls WHERE status='pending')

---

## User Workflows

### Retrying a Failed Task

1. **Task List View**
   - Failed task shows red "Failed" badge
   - Click task row to open detail modal
   - Or use bulk select and Retry action button

2. **Single Task Retry (Detail Modal)**
   - Open TaskDetailModal by clicking task row
   - Click "Retry" in TaskControlPanel
   - System routes through validated status endpoint
   - Retry counter badge appears: "Retry #1"
   - Task returns to pending status for executor pickup

3. **Bulk Retry**
   - Select multiple failed tasks via checkboxes
   - Click "Actions" → "Retry"
   - Confirmation dialog shows selected count
   - All tasks routed through unified status service
   - Retry metadata persisted for each task

### Monitoring Task Progress

1. **Task List View**
   - Status badge shows current state (e.g., "In Progress")
   - Retry badge appears if retry_count > 0
   - Step label displays current phase (e.g., "Generating content")
   - Progress bar shows percentage with stage-based color
   - Animated shimmer effect indicates active processing

2. **Detail Modal View**
   - Progress bar in header with live percentage
   - Current stage message below title
   - Timeline tab shows pulsing indicator for active tasks
   - "Current Execution Stage" card displays:
     - Icon and stage name
     - Percentage complete badge
     - Current execution message

### Queue Management

1. **Pausing Active Tasks**
   - Click "Pause" on in_progress or pending task
   - Status changes to "On Hold"
   - Executor skips task in next polling cycle
   - Progress preserved in metadata

2. **Resuming Paused Tasks**
   - Click "Resume" on on_hold task
   - Status changes to "Pending" (not in_progress)
   - Executor picks up task in next polling cycle (5-second interval)
   - Previous progress and metadata preserved

---

## Data Model

### task_metadata JSON Structure

```json
{
  "retry_count": 2,
  "last_retry_at": "2026-03-08T15:30:45.123Z",
  "last_retry_by": "oversight_hub_user",
  "stage": "content_generation",
  "message": "Generating content",
  "percentage": 45,
  "status": "in_progress",
  "started_at": "2026-03-08T15:30:00.000Z",
  "validation_details": {
    "gates": [
      {
        "name": "length",
        "passed": true,
        "message": "Content meets length requirements"
      },
      { "name": "style", "passed": true, "message": "Style analysis passed" }
    ]
  }
}
```

### Status Transition Rules

```
pending → in_progress    (executor picks up)
in_progress → completed  (success)
in_progress → failed     (error/validation failure)
failed → pending         (retry action)
in_progress → on_hold    (pause action)
on_hold → pending        (resume action)
pending → cancelled      (cancel action)
```

### Stage Progression

1. **queued** (5%) - Initial acceptance
2. **content_generation** (20%) - Main processing
3. **finalizing** (90%) - Post-processing
4. **complete** (100%) - Done

---

## API Endpoints

### Status Change (Validated)

```http
POST /api/tasks/{task_id}/status/validated
Content-Type: application/json

{
  "new_status": "pending",
  "action": "retry",
  "changed_by": "oversight_hub_user",
  "reason": "User-initiated retry",
  "metadata": {
    "custom_field": "value"
  }
}
```

**Response:**

```json
{
  "success": true,
  "task_id": "b2437981-feba-4a8d-855e-940e3c417907",
  "old_status": "failed",
  "new_status": "pending",
  "changes_logged": true,
  "task_metadata": {
    "retry_count": 1,
    "last_retry_at": "2026-03-08T15:30:45.123Z",
    "last_retry_by": "oversight_hub_user"
  }
}
```

### Bulk Actions

```http
POST /api/tasks/bulk
Content-Type: application/json

{
  "task_ids": ["id1", "id2", "id3"],
  "action": "retry"
}
```

**Response:**

```json
{
  "updated": 3,
  "failed": 0,
  "message": "Successfully updated 3 tasks"
}
```

---

## Visual Design

### Status Badge Colors

| Status            | Color               | Background                | Use Case                   |
| ----------------- | ------------------- | ------------------------- | -------------------------- |
| Pending           | Yellow (#ffa726)    | rgba(255, 167, 38, 0.15)  | Queued for execution       |
| In Progress       | Cyan (#00d9ff)      | rgba(0, 217, 255, 0.15)   | Active processing          |
| Completed         | Green (#66bb6a)     | rgba(102, 187, 106, 0.15) | Success                    |
| Failed            | Red (#f44336)       | rgba(244, 67, 54, 0.15)   | Error/validation failure   |
| On Hold           | Gray (#9e9e9e)      | rgba(158, 158, 158, 0.15) | Paused                     |
| Awaiting Approval | Blue (#42a5f5)      | rgba(66, 165, 245, 0.15)  | Human review required      |
| Approved          | Teal (#26a69a)      | rgba(38, 166, 154, 0.15)  | Approved, ready to publish |
| Published         | Purple (#ab47bc)    | rgba(171, 71, 188, 0.15)  | Live on website            |
| Cancelled         | Dark Gray (#616161) | rgba(97, 97, 97, 0.15)    | User cancelled             |

### Progress Bar States

**Queued** (Orange gradient):

```css
background: linear-gradient(90deg, #ffa726, #ffb74d);
box-shadow: 0 0 8px rgba(255, 167, 38, 0.4);
```

**Content Generation** (Blue gradient):

```css
background: linear-gradient(90deg, #42a5f5, #64b5f6);
box-shadow: 0 0 8px rgba(66, 165, 245, 0.4);
```

**Finalizing** (Green gradient):

```css
background: linear-gradient(90deg, #66bb6a, #81c784);
box-shadow: 0 0 8px rgba(102, 187, 106, 0.4);
```

### Animations

**Shimmer Effect** (active progress bars):

```css
@keyframes shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}
```

**Pulse Effect** (Timeline tab indicator):

```css
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
```

---

## Testing

### Manual Testing Checklist

- [ ] Retry button increments retry_count in metadata
- [ ] Retry badge displays in task list and detail modal
- [ ] Resume action sets pending (executor picks up task)
- [ ] Progress bar shows stage-based colors
- [ ] Step text displays for pending/in_progress/running tasks
- [ ] Progress percentage updates during execution
- [ ] Timeline tab shows pulsing indicator for active tasks
- [ ] Current Execution Stage card displays correct information
- [ ] Bulk retry updates all selected tasks
- [ ] Validation failures display with structured gate feedback

### Automated Test Coverage

**Backend Tests:**

- `test_enhanced_status_change_service.py` - Status transitions and metadata merge
- `test_task_executor.py` - Stage progression and progress updates
- `test_bulk_task_routes.py` - Bulk action handlers

**Frontend Tests (Planned):**

- TaskManagement component - Metadata extraction and display
- TaskDetailModal component - Progress bar rendering
- Status badge rendering - All 9 status states
- Retry counter badge - Display logic

---

## Configuration

### Environment Variables

```env
# Executor polling interval (seconds)
TASK_EXECUTOR_POLL_INTERVAL=5

# Enable/disable progress tracking
ENABLE_TASK_PROGRESS_TRACKING=true

# WebSocket real-time updates
ENABLE_WEBSOCKET_PROGRESS=true
```

### Feature Flags

All retry and status visibility features are enabled by default. No feature flags required.

---

## Performance Considerations

### Frontend

- **Metadata Parsing**: Safe JSON.parse with try-catch, falls back to empty object
- **CSS Classes**: Normalized once per render (getStatusClass memoization candidate)
- **Progress Bars**: CSS animations use GPU-accelerated transforms
- **Re-renders**: React memo candidates for status badge and retry counter components

### Backend

- **Metadata Updates**: Single UPDATE with JSONB merge, no separate transactions
- **WebSocket Emission**: Fire-and-forget pattern, doesn't block task execution
- **Stage Updates**: Incremental writes (3-4 updates per task execution)
- **Audit Trail**: Async logging, doesn't impact response time

### Database

- **JSONB Indexing**: Consider GIN index on task_metadata for metadata queries
- **Query Optimization**: Status and metadata filters use existing indexes
- **Connection Pooling**: Shared asyncpg pool across all status operations

---

## Troubleshooting

### Retry Counter Not Incrementing

**Symptom**: Retry button works but retry_count stays 0

**Diagnosis**:

```sql
SELECT task_metadata FROM content_tasks WHERE id = 'task-id';
```

**Solution**:

- Ensure `action="retry"` parameter in status change request
- Verify enhanced_status_change_service merges metadata (not replaces)
- Check audit logs for status change confirmation

### Progress Bar Not Updating

**Symptom**: Progress bar stuck at 0% or old value

**Diagnosis**:

```sql
SELECT task_metadata->>'percentage' as percentage,
       task_metadata->>'stage' as stage
FROM content_tasks WHERE id = 'task-id';
```

**Solution**:

- Verify task_executor calls update_processing_stage()
- Check WebSocket connection status in browser console
- Confirm ENABLE_TASK_PROGRESS_TRACKING=true in .env.local

### Resume Action Not Working

**Symptom**: Resumed task stays on_hold, executor doesn't pick up

**Diagnosis**:

```sql
SELECT status FROM content_tasks WHERE id = 'task-id';
-- Should be 'pending' after resume, not 'in_progress'
```

**Solution**:

- Check bulk_task_routes.py status_map["resume"] = "pending"
- Verify executor polling query: WHERE status='pending'
- Restart executor if stuck: npm run dev:cofounder

### Step Text Not Showing

**Symptom**: Status badge visible but no step text below

**Diagnosis**:

- Check task status is pending/in_progress/running
- Verify task_metadata.message or task_metadata.stage exists
- Inspect browser console for parsing errors

**Solution**:

- Task must be in active state for step text
- Backend must write stage/message to metadata
- Check getTaskStepLabel() logic in TaskManagement.jsx

---

## Future Enhancements

### Planned Features

1. **Retry Limits** - Maximum retry attempts per task (configurable)
2. **Exponential Backoff** - Delay between retry attempts
3. **Bulk Pause/Resume** - Multi-select queue management
4. **Progress History** - Chart showing progress over time
5. **Stage Timing** - Duration metrics for each execution stage
6. **Notification System** - Alert on retry exhaustion or long-running tasks

### Known Limitations

- Retry counter doesn't automatically reset after success (by design, for audit trail)
- Progress bar animation may lag on slow networks (WebSocket latency)
- Step text truncates with ellipsis if too long (CSS overflow)
- No retry history UI (shows count only, not individual retry details)

---

## References

### Related Documentation

- [System Design](../02-Architecture/System-Design.md)
- [WebSocket Real-Time Updates](WebSocket-Real-Time.md)
- [Workflows System](Workflows-System.md)
- [Operations and Maintenance](../05-Operations/Operations-Maintenance.md)

### Implementation Files

**Frontend:**

- `web/oversight-hub/src/routes/TaskManagement.jsx`
- `web/oversight-hub/src/routes/TaskManagement.css`
- `web/oversight-hub/src/components/tasks/TaskDetailModal.jsx`
- `web/oversight-hub/src/components/tasks/StatusComponents.jsx`

**Backend:**

- `src/cofounder_agent/services/enhanced_status_change_service.py`
- `src/cofounder_agent/services/task_executor.py`
- `src/cofounder_agent/routes/bulk_task_routes.py`
- `src/cofounder_agent/routes/task_routes.py`

### Version History

- **3.1.0** (March 8, 2026) - Initial release with full retry and visibility features
- **3.0.2** (March 7, 2026) - Foundation (Phase 2B database tests complete)

---

## Support

For issues or questions:

- Check [Troubleshooting Guide](../06-Troubleshooting/README.md)
- Review [GitHub Issues](https://github.com/Glad-Labs/glad-labs-codebase/issues)
- Contact: developers@glad-labs.com
