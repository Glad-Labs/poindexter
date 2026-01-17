# Approval Workflow Comparison - Visual Guide

**Date:** January 16, 2026

---

## Current vs Proposed Architecture

### CURRENT STATE (As Is)

```
┌─────────────────────────────────────────────────────┐
│          OVERSIGHT HUB UI (React)                    │
├─────────────────────────────────────────────────────┤
│                                                      │
│  OrchestratorPage (Approval)                        │
│  ├─ Approve Button ────→ handleApprove()           │
│  ├─ Reject Button  ────→ handleReject()            │
│  └─ Status: pending_approval, approved, etc.       │
│                                                      │
│  TaskActions (Dialogs)                             │
│  ├─ Approve Dialog ────→ onApprove()               │
│  ├─ Reject Dialog  ────→ onReject()                │
│  └─ Delete Dialog  ────→ onDelete()                │
│                                                      │
│  TaskManagement (List)                             │
│  ├─ Status Badges                                   │
│  ├─ Edit/Delete Buttons                             │
│  └─ NO approval workflow                            │
│                                                      │
└────────────┬─────────────────────────────────────┬──┘
             │                                      │
    OLD API (Limited)                       NEW API (Phase 4)
             │                                      │
┌────────────▼──────────────┐    ┌────────────────▼────────────────┐
│ /api/orchestrator/        │    │ /api/tasks/{id}/status/*        │
│  executions/{id}/approve  │    │ (Backend validation)             │
│  executions/{id}/reject   │    │                                  │
└────────────┬──────────────┘    └────────────┬────────────────────┘
             │                                │
             │ LIMITED HISTORY               │ FULL AUDIT TRAIL
             │ NO METADATA                   │ JSONB METADATA
             │ NO ERROR TRACKING             │ ERROR TRACKING
             │ NO STATUS VALIDATION          │ STATUS VALIDATION
             │                                │
        DATABASE                         DATABASE
        (Partial)                       (Comprehensive)
```

---

## Status Value Mapping

### OLD SYSTEM (Current)

```
pending_approval  ──┐
                    ├─→  Approval workflow only
approved          ──┘

executing         ──┐
                    ├─→  Execution workflow only
completed         ──┘

failed            ──→   Error state
```

### NEW SYSTEM (Phase 5)

```
pending           ──→   Initial state
    ↓
in_progress       ──→   Processing
    ↓
awaiting_approval ──→   Needs approval ⭐
    ├─ approved    ──→   Approved ✓
    └─ rejected    ──→   Rejected ✗

on_hold           ──→   Paused state

published         ──→   Complete success
failed            ──→   Error state
cancelled         ──→   Cancelled state
```

### PROPOSED UNIFIED SYSTEM (Phase 6)

```
STATUS ENUM:
  PENDING              = 'pending'              [Initial]
  IN_PROGRESS          = 'in_progress'          [Running]
  AWAITING_APPROVAL    = 'awaiting_approval'    [Needs review]
  APPROVED             = 'approved'             [Approved]
  REJECTED             = 'rejected'             [Rejected]
  PUBLISHED            = 'published'            [Success]
  FAILED               = 'failed'               [Error]
  ON_HOLD              = 'on_hold'              [Paused]
  CANCELLED            = 'cancelled'            [Stopped]

BACKWARD COMPATIBILITY MAP:
  pending_approval  → awaiting_approval
  approved          → approved (matches)
  executing         → in_progress
  completed         → published
  failed            → failed (matches)
```

---

## Component Comparison

### EXISTING COMPONENTS

```
TaskActions.jsx
├─ Purpose: Approval/rejection dialogs
├─ Props: onApprove, onReject, onDelete, isLoading
├─ API: Direct calls to /api/orchestrator
├─ Validation: Minimal (frontend only)
└─ History: None (read-only after action)

OrchestratorPage.jsx
├─ Purpose: Main approval workflow UI
├─ Features: Status display, manual approve/reject
├─ API: /api/orchestrator/executions
├─ Polling: 5-second refresh
└─ Storage: In-memory state only

TaskDetailModal.jsx
├─ Purpose: Show task details
├─ Content: Basic task info only
├─ Features: Status badge, error panel
└─ No history tracking
```

### NEW COMPONENTS (Phase 5)

```
StatusAuditTrail.jsx
├─ Purpose: Complete audit trail display
├─ Features: Timeline, filters, metadata, timestamps
├─ API: GET /api/tasks/{id}/status-history
├─ Storage: Database persistence
└─ Data: Full history with context

StatusTimeline.jsx
├─ Purpose: Visual status flow
├─ Features: All states, durations, pulse animation
├─ API: None (uses passed props)
└─ Display: Interactive state details

ValidationFailureUI.jsx
├─ Purpose: Error/validation failure display
├─ Features: Severity, type, recommendations
├─ API: GET /api/tasks/{id}/status-history/failures
└─ Data: Structured error information

StatusDashboardMetrics.jsx
├─ Purpose: KPI dashboard
├─ Features: Counts, rates, time ranges
├─ API: None (calculated from history)
└─ Display: Progress bars, cards
```

---

## User Flow Comparison

### CURRENT APPROVAL FLOW

```
1. User opens OrchestratorPage
                ↓
2. Sees list of tasks with status
                ↓
3. Clicks "Approve" button
                ↓
4. Dialog appears (TaskActions)
                ↓
5. User confirms approval
                ↓
6. API call: POST /api/orchestrator/executions/{id}/approve
                ↓
7. Status updates to "approved"
                ↓
8. UI refreshes (5s poll or manual)
                ↓
9. History lost ❌ (No audit trail)
```

### NEW APPROVAL FLOW (What Will Be)

```
1. User opens TaskDetailModal
                ↓
2. Tabs show: Overview | Timeline | History | Failures
                ↓
3. Clicks "Approve" button
                ↓
4. Enhanced dialog appears (with validation)
                ↓
5. User confirms + adds feedback
                ↓
6. API call: PUT /api/tasks/{id}/status/validated
                ↓
7. Backend validates transition
                ↓
8. Status updates + History logged ✓
                ↓
9. StatusAuditTrail shows new entry
   StatusTimeline updates
   ValidationFailureUI shows any errors
                ↓
10. Full audit trail available ✅
```

---

## API Comparison

### OLD APPROVAL ENDPOINT

```http
POST /api/orchestrator/executions/{executionId}/approve

Request:
(No body required)

Response:
{
  "status": "success",
  "message": "Task approved"
}

Issues:
❌ No validation details
❌ No history stored
❌ No error tracking
❌ No metadata capture
```

### NEW APPROVAL ENDPOINT

```http
PUT /api/tasks/{taskId}/status/validated

Request:
{
  "new_status": "approved",
  "reason": "Passed quality check",
  "feedback": "Good work!",
  "user_id": "reviewer-123",
  "metadata": {
    "reviewer_role": "senior_editor",
    "review_duration_minutes": 15
  }
}

Response:
{
  "success": true,
  "task_id": "task-123",
  "old_status": "awaiting_approval",
  "new_status": "approved",
  "timestamp": "2025-01-16T10:00:00Z",
  "validation_details": {
    "passed_rules": [...],
    "failed_rules": []
  }
}

Benefits:
✅ Full validation details
✅ History automatically stored
✅ Error tracking included
✅ Metadata captured
✅ Audit trail complete
```

---

## Data Flow Diagram

### CURRENT STATE

```
User Interface
    ↓
    ├─ OrchestratorPage ──→ handleApprove()
    │       ↓
    │   TaskActions Dialog
    │       ↓
    │   User clicks Approve
    │       ↓
    └─→ POST /api/orchestrator/executions/{id}/approve
            ↓
        Backend (Simple Update)
            ↓
        Database (Status only, minimal metadata)
            ↓
        Response: {"status": "success"}
            ↓
        UI Refresh (5-second poll)
            ↓
        Display updated status badge

        ❌ No History
        ❌ No Audit Trail
        ❌ No Validation Details
```

### PROPOSED NEW STATE

```
User Interface
    ├─ TaskDetailModal
    │   ├─ Overview Tab (existing)
    │   ├─ Timeline Tab ←─────── StatusTimeline ✅
    │   ├─ History Tab ←────────── StatusAuditTrail ✅
    │   └─ Failures Tab ←──── ValidationFailureUI ✅
    │
    └─ User clicks Approve
            ↓
        Enhanced Dialog (TaskActions v2)
            ├─ Feedback field
            ├─ Reason field
            └─ Validation preview
                ↓
            PUT /api/tasks/{id}/status/validated
                ↓
            Backend
            ├─ StatusTransitionValidator (validation)
            ├─ EnhancedStatusChangeService (orchestration)
            └─ tasks_db (persistence)
                ↓
            Database
            ├─ task_status_history table (full record)
            ├─ metadata JSONB (all context)
            └─ indexes (performance)
                ↓
            Response: {success, validation_details, history_entry}
                ↓
            UI Updates
            ├─ StatusAuditTrail (new entry appears)
            ├─ StatusTimeline (new state shows)
            ├─ ValidationFailureUI (errors if any)
            └─ StatusDashboardMetrics (counts update)
                ↓
            ✅ Complete Audit Trail
            ✅ Validation Details Visible
            ✅ Error Tracking Active
```

---

## Approval Dialog Evolution

### CURRENT (TaskActions)

```
┌──────────────────────────────────┐
│  Approve Task                    │
├──────────────────────────────────┤
│                                  │
│  Are you sure?                   │
│                                  │
│  Feedback (optional):            │
│  ┌──────────────────────────────┐│
│  │ Text field (multiline)       ││
│  └──────────────────────────────┘│
│                                  │
│  [Cancel] [Approve] (green)      │
└──────────────────────────────────┘
```

### PROPOSED ENHANCED (v2)

```
┌──────────────────────────────────┐
│  Approve Task                    │
├──────────────────────────────────┤
│                                  │
│  ✓ All validations passed        │ ← NEW
│                                  │
│  Status: pending → awaiting_approval → approved
│                                  │
│  Reason:                         │
│  ┌──────────────────────────────┐│
│  │ Passed quality check         ││
│  └──────────────────────────────┘│
│                                  │
│  Feedback:                       │
│  ┌──────────────────────────────┐│
│  │ Text field (multiline)       ││
│  └──────────────────────────────┘│
│                                  │
│  Metadata:                       │ ← NEW
│  ├─ Reviewer: John Doe           │
│  ├─ Role: Senior Editor          │
│  └─ Duration: 12 minutes         │
│                                  │
│  [Cancel] [Approve] (green)      │
└──────────────────────────────────┘
```

---

## Integration Points

### Point 1: Status Updates

```
CURRENT              PROPOSED              UNIFIED
────────             ────────              ───────
TaskActions    →     TaskActions v2    →   UnifiedApprovalDialog
   ↓                    ↓                       ↓
handleApprove()   handleApprove()   →   updateTaskStatus()
   ↓                    ↓                       ↓
/api/orchestrator  /api/tasks/{id}   →   Same endpoint
                    /status/validated       (unified)
```

### Point 2: History Display

```
CURRENT              PROPOSED              UNIFIED
────────             ────────              ───────
TaskDetailModal  →   + StatusAuditTrail →  TaskDetailModal
(No history)         (New component)         (with tabs)
                                                ↓
                                            Show history
                                            in dedicated tab
```

### Point 3: Status Dashboard

```
CURRENT              PROPOSED              UNIFIED
────────             ────────              ───────
TaskManagement   →   + Dashboard metrics →  Enhanced
(Basic stats)        (New component)         Dashboard
                                                ↓
                                            Show metrics
                                            + history
```

---

## Timeline for Integration

### PHASE 5 (CURRENT) ✅

```
✓ StatusAuditTrail created
✓ StatusTimeline created
✓ ValidationFailureUI created
✓ StatusDashboardMetrics created
✓ Backend APIs ready
✓ Database migration ready
```

### PHASE 6 (NEXT) 🔄

```
[ ] Run new components in parallel
[ ] Create unified status service
[ ] Add StatusAuditTrail to TaskDetailModal
[ ] Enhance TaskActions dialogs
[ ] Add metrics to TaskManagement
[ ] Map old statuses to new ones
[ ] Test approval workflows
[ ] User acceptance testing
```

### PHASE 7+ (FUTURE) ⏳

```
[ ] Consolidate endpoints
[ ] Remove old approval APIs
[ ] Deprecate old status values
[ ] Archive old workflow code
[ ] Full migration complete
```

---

## Summary

| Aspect                | Current | New (Phase 5)   | Unified (Phase 6) |
| --------------------- | ------- | --------------- | ----------------- |
| **Status Tracking**   | Minimal | Complete ✓      | Complete + UI     |
| **Approval Dialog**   | Basic   | Via backend     | Enhanced          |
| **History**           | None    | Stored ✓        | Displayed         |
| **Validation**        | Minimal | Comprehensive ✓ | Comprehensive     |
| **Dashboard**         | No      | Metrics ✓       | Integrated        |
| **Error Tracking**    | No      | Detailed ✓      | Detailed          |
| **Audit Trail**       | No      | Full ✓          | Full + UI         |
| **Real-time Updates** | 5s poll | On-demand       | Optimized         |

**Status:** Systems are complementary and ready for integration ✅

---

**Analysis:** January 16, 2026  
**Next Step:** Create Phase 6 Integration Plan
