# Task Status State Machine & UI Color Guide

**Last Updated:** January 17, 2026  
**Purpose:** Define proper separation of task states and their UI representation

---

## State Hierarchy & Distinctions

### 1. **Approval Workflow** (With Human Review)
For content that requires human approval before publishing.

```
pending/generating 
    ↓
awaiting_approval (⚠️ Orange - needs review)
    ↓
    ├─ approved (✓ Blue - human approved, pending publication)
    │   ↓
    │   published (✓✓ Cyan - live on CMS)
    │
    └─ rejected (✗ Red - human rejected)
```

**Colors:**
- **awaiting_approval**: Orange (#ff9800) - Pulsing animation
- **approved**: Bright Blue (#1e88e5) - Bold, waiting to publish
- **published**: Cyan (#00bcd4) - Live on CMS ✓ DISTINCT FROM COMPLETED
- **rejected**: Red (#ff5722) - Human decision

---

### 2. **Direct Completion** (No Approval)
For tasks that complete without human approval.

```
pending/generating 
    ↓
completed (✓ Lime Green #7cb342 - task finished)
```

**Color:**
- **completed**: Lime Green (#7cb342) - Single task finished ✓ DISTINCT FROM PUBLISHED

---

### 3. **Error States**
For failed tasks that need attention.

```
pending/generating 
    ↓
failed (✗ Red #f44336 - error occurred)
```

---

### 4. **Management States**
For paused, cancelled, or flagged tasks.

```
- on_hold (⊥ Gray #9e9e9e - paused)
- cancelled (⊙ Gray #616161 - cancelled)
- paused (⊥ Gray #9e9e9e - paused)
```

---

## Visual Reference

### Status Badge Colors (UI Table)

| Status | Icon | Color | Background | Border | Meaning | Workflow |
|--------|------|-------|-----------|--------|---------|----------|
| pending | ⧗ | #ffc107 | Amber 15% | #ffc107 | Waiting to start | All |
| in_progress | ⟳ | #2196f3 | Blue 15% | #2196f3 | Currently processing | All |
| awaiting_approval | ⚠ | #ff6f00 | Orange 20% | #ff6f00 | Needs human review | Approval |
| **approved** | **✓** | **#1e88e5** | **Blue 20%** | **#1e88e5** | Human approved | Approval |
| **published** | **✓✓** | **#00bcd4** | **Cyan 15%** | **#00bcd4** | Live on CMS | Approval |
| **completed** | **✓** | **#7cb342** | **Lime 15%** | **#7cb342** | Task finished | Direct |
| failed | ✗ | #f44336 | Red 15% | #f44336 | Error occurred | All |
| rejected | ✗ | #ff5722 | Orange-Red 15% | #ff5722 | Human rejected | Approval |
| on_hold | ⊥ | #9e9e9e | Gray 15% | #9e9e9e | Paused | Management |
| cancelled | ⊙ | #616161 | Dark Gray 15% | #616161 | Cancelled | Management |

---

## Key Distinctions

### ✓ vs ✓✓ Icons
- **✓** (single check) = Task completed (one-off, no publishing)
- **✓✓** (double check) = Published to CMS (confirmed live)
- **✓** (single check) = Human approved (ready for next step)

### Color Meanings
- **Orange (#ff9800)** = Awaiting action (approval needed)
- **Blue (#1e88e5)** = Approved but not live (pending action)
- **Cyan (#00bcd4)** = Live & published (final state for approval workflow)
- **Lime (#7cb342)** = Completed (final state for non-approval tasks)
- **Red (#f44336)** = Error state (failed task)

---

## Backend Implementation

### Task Status Enum (task_status.py)

```python
class TaskStatus(str, Enum):
    # Initial
    PENDING = "pending"
    
    # Processing
    GENERATING = "generating"
    IN_PROGRESS = "in_progress"
    
    # Approval workflow (for content)
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"      # Human decision: approved
    REJECTED = "rejected"       # Human decision: rejected
    
    # Terminal states
    PUBLISHED = "published"     # Approval workflow → live
    COMPLETED = "completed"     # Non-approval → finished
    FAILED = "failed"
    
    # Management
    PAUSED = "paused"
    CANCELLED = "cancelled"
```

### Frontend Mapping (TaskList.jsx)

```javascript
const getStatusColor = (status) => {
  const statusMap = {
    pending: 'status-pending',
    awaiting_approval: 'status-awaiting-approval',  // Orange
    approved: 'status-approved',                     // Blue
    published: 'status-published',                   // Cyan
    completed: 'status-completed',                   // Lime
    failed: 'status-failed',
    rejected: 'status-rejected',
  };
  return statusMap[statusLower] || 'status-default';
};
```

---

## Workflow Examples

### Example 1: Blog Post (Approval Workflow)

```
1. User creates blog post
   → Status: generating (in progress)

2. Generation completes
   → Status: awaiting_approval (Orange badge)
   → Icon: ⚠
   → Awaits human review

3. Human reviews and approves
   → Status: approved (Blue badge)
   → Icon: ✓
   → Waiting to publish

4. System publishes to CMS
   → Status: published (Cyan badge)
   → Icon: ✓✓
   → Live on website
```

### Example 2: Direct Completion Task

```
1. Task starts
   → Status: pending (Amber)

2. Task processing
   → Status: in_progress (Blue)

3. Task finishes naturally
   → Status: completed (Lime badge)
   → Icon: ✓
   → No further action needed
```

---

## UI Update Plan

- [x] **TaskList.jsx**: Updated status color mapping to NOT map completed → published
- [x] **TaskList.jsx**: Updated icon comments to clarify distinctions
- [x] **TaskManagement.css**: Updated badge colors
  - `status-completed`: Changed to Lime (#7cb342) for distinction
  - `status-published`: Changed to Cyan (#00bcd4) for CMS live state
  - `status-approved`: Kept Blue (#1e88e5) for human approval
- [x] **TaskManagement.css**: Updated table row borders to match badge colors

---

## Testing Checklist

- [ ] Navigate to http://localhost:3001/tasks
- [ ] Verify "awaiting_approval" tasks show Orange badge with ⚠ icon
- [ ] Verify "approved" tasks show Blue badge with ✓ icon
- [ ] Verify "published" tasks show Cyan badge with ✓✓ icon
- [ ] Verify "completed" tasks show Lime badge with ✓ icon
- [ ] Verify table row left border matches badge color
- [ ] Verify no tasks are showing both blue and green colors for same state
- [ ] Verify clicking approve transitions: awaiting_approval → approved
- [ ] Verify publishing transitions: approved → published

---

## Deployment Notes

**No Database Changes Required:** All status values already exist in `TaskStatus` enum.

**Frontend Only:** CSS colors and component mappings updated. Requires webpack rebuild.

**Backward Compatibility:** Old tasks with "completed" status will now show correct Lime green instead of merged with published (Cyan).

---

## References

- Backend Status Enum: [src/cofounder_agent/schemas/task_status.py](src/cofounder_agent/schemas/task_status.py)
- Frontend Mapping: [web/oversight-hub/src/components/tasks/TaskList.jsx](web/oversight-hub/src/components/tasks/TaskList.jsx)
- CSS Colors: [web/oversight-hub/src/routes/TaskManagement.css](web/oversight-hub/src/routes/TaskManagement.css)

