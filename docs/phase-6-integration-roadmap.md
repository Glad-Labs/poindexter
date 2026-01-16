# Phase 6 Integration Roadmap

**Title:** Unifying Approval Workflows  
**Status:** Planning (Ready to Execute)  
**Target Completion:** 2-3 weeks  
**Dependencies:** Phase 5 Complete ✅

---

## Executive Summary

The new Status Management System (Phase 5) and existing approval workflow (OrchestratorPage/TaskActions) should be integrated to create a unified, comprehensive approval experience with full audit trails, validation feedback, and metrics dashboards.

**Goal:** Single, authoritative approval system with complete history tracking.

---

## Phase 6 Detailed Tasks

### TASK 1: Status Value Mapping (Week 1, Day 1-2)

**Objective:** Reconcile 5 old statuses with 9 new statuses

**Actions:**

1. Create migration script

```sql
-- Map existing statuses to new system
UPDATE tasks SET status = 'awaiting_approval' WHERE status = 'pending_approval';
UPDATE tasks SET status = 'in_progress' WHERE status = 'executing';
UPDATE tasks SET status = 'published' WHERE status = 'completed';
-- Keep 'approved' and 'failed' as-is
```

2. Create backward compatibility layer

```python
# In backend
STATUS_MAP_OLD_TO_NEW = {
    'pending_approval': 'awaiting_approval',
    'approved': 'approved',
    'executing': 'in_progress',
    'completed': 'published',
    'failed': 'failed',
}

STATUS_MAP_NEW_TO_OLD = {v: k for k, v in STATUS_MAP_OLD_TO_NEW.items()}
```

3. Update frontend constants

```javascript
// web/oversight-hub/src/Constants/statusEnums.js
export const STATUS_ENUM = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  AWAITING_APPROVAL: 'awaiting_approval',
  APPROVED: 'approved',
  REJECTED: 'rejected',
  PUBLISHED: 'published',
  FAILED: 'failed',
  ON_HOLD: 'on_hold',
  CANCELLED: 'cancelled',
};

// For backward compatibility
export const STATUS_ENUM_LEGACY = {
  PENDING_APPROVAL: 'pending_approval',
  APPROVED: 'approved',
  EXECUTING: 'executing',
  COMPLETED: 'completed',
  FAILED: 'failed',
};
```

**Deliverable:** Compatibility layer allowing both systems to coexist

**Testing:**

- [ ] Old status values still work
- [ ] New status values work
- [ ] Mapping is bidirectional
- [ ] No data loss

---

### TASK 2: Create Unified Status Service (Week 1, Day 2-3)

**Objective:** Single service for all status operations

**File:** `web/oversight-hub/src/services/unifiedStatusService.js`

```javascript
/**
 * Unified Status Service
 * Abstracts approval workflow, validates transitions, handles errors
 */

export const unifiedStatusService = {
  /**
   * Update task status with validation
   */
  async updateStatus(taskId, newStatus, options = {}) {
    const {
      reason = '',
      feedback = '',
      userId = null,
      metadata = {},
    } = options;

    try {
      const response = await fetch(`/api/tasks/${taskId}/status/validated`, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          new_status: newStatus,
          reason,
          feedback,
          user_id: userId || getCurrentUserId(),
          metadata,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Status update failed');
      }

      return await response.json();
    } catch (error) {
      console.error('Status update error:', error);
      throw error;
    }
  },

  /**
   * Approve a task
   */
  async approve(taskId, feedback = '', userId = null) {
    return this.updateStatus(taskId, 'approved', {
      reason: 'Task approved',
      feedback,
      userId,
      metadata: { action: 'approve', timestamp: new Date().toISOString() },
    });
  },

  /**
   * Reject a task
   */
  async reject(taskId, reason = '', userId = null) {
    if (!reason.trim()) {
      throw new Error('Rejection reason is required');
    }
    return this.updateStatus(taskId, 'rejected', {
      reason,
      userId,
      metadata: { action: 'reject', timestamp: new Date().toISOString() },
    });
  },

  /**
   * Get task history
   */
  async getHistory(taskId, limit = 50) {
    const response = await fetch(
      `/api/tasks/${taskId}/status-history?limit=${limit}`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );
    return response.json();
  },

  /**
   * Get validation failures
   */
  async getFailures(taskId, limit = 50) {
    const response = await fetch(
      `/api/tasks/${taskId}/status-history/failures?limit=${limit}`,
      {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('authToken')}`,
        },
      }
    );
    return response.json();
  },
};
```

**Deliverable:** Single point of entry for all status operations

**Testing:**

- [ ] Approve function works
- [ ] Reject function works
- [ ] History fetch works
- [ ] Error handling works
- [ ] Metadata saved

---

### TASK 3: Update OrchestratorPage (Week 1, Day 3-4)

**Objective:** Integrate new status service into approval workflow

**Changes:**

```javascript
// OLD
import { makeRequest } from '../services/cofounderAgentClient';

const handleApprove = async (executionId) => {
  await makeRequest(
    `/api/orchestrator/executions/${executionId}/approve`,
    'POST'
  );
};

// NEW
import { unifiedStatusService } from '../services/unifiedStatusService';

const handleApprove = async (executionId) => {
  try {
    const result = await unifiedStatusService.approve(
      executionId,
      approveFeedback
    );
    alert('✅ Task approved successfully');
    await loadOrchestrations();
  } catch (error) {
    alert(`❌ Error: ${error.message}`);
  }
};
```

**Steps:**

1. Import `unifiedStatusService`
2. Update `handleApprove()`
3. Update `handleReject()`
4. Keep UI same (no breaking changes)
5. Add validation feedback display

**Deliverable:** OrchestratorPage using new unified service

**Testing:**

- [ ] Approve button still works
- [ ] Reject button still works
- [ ] UI refreshes correctly
- [ ] History is captured

---

### TASK 4: Enhance TaskActions Dialogs (Week 1, Day 4)

**Objective:** Add validation feedback to existing dialogs

**Changes:**

```javascript
// In TaskActions.jsx

const handleApproveSubmit = async () => {
  try {
    setError('');

    // NEW: Use unified service
    const result = await unifiedStatusService.approve(
      selectedTask.id,
      feedback
    );

    // NEW: Show validation details if any
    if (result.validation_details?.warnings) {
      setWarning(result.validation_details.warnings.join(', '));
    }

    handleCloseDialog();
    onClose();
  } catch (err) {
    setError(err.message);
  }
};
```

**Deliverable:** Enhanced dialogs with validation feedback

**Testing:**

- [ ] Dialogs still appear
- [ ] Validation feedback shows
- [ ] Errors displayed correctly
- [ ] Warnings shown

---

### TASK 5: Update TaskDetailModal (Week 2, Day 1-2)

**Objective:** Add history tabs with new components

**File:** Update `web/oversight-hub/src/components/tasks/TaskDetailModal.jsx`

```javascript
import { Tabs, Tab, TabPanel, Box } from '@mui/material';
import {
  StatusAuditTrail,
  StatusTimeline,
  ValidationFailureUI,
} from './StatusComponents';

const TaskDetailModal = ({ onClose }) => {
  const [tabValue, setTabValue] = useState(0);
  const { selectedTask } = useStore();

  if (!selectedTask) return null;

  return (
    <Dialog open={!!selectedTask} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Task Details: {selectedTask.topic}</DialogTitle>

      <DialogContent>
        <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
          <Tabs
            value={tabValue}
            onChange={(e, v) => setTabValue(v)}
            aria-label="task details tabs"
          >
            <Tab label="Overview" id="tab-0" />
            <Tab label="Timeline" id="tab-1" />
            <Tab label="History" id="tab-2" />
            <Tab label="Errors" id="tab-3" />
          </Tabs>
        </Box>

        {/* Tab 0: Overview (existing) */}
        <TabPanel value={0} sx={{ mt: 2 }}>
          <p>
            <strong>Status:</strong> {renderStatus(selectedTask.status)}
          </p>
          <p>
            <strong>ID:</strong> {selectedTask.id}
          </p>
          {/* ... rest of existing content ... */}
        </TabPanel>

        {/* Tab 1: Timeline (NEW) */}
        <TabPanel value={1} sx={{ mt: 2 }}>
          <StatusTimeline
            currentStatus={selectedTask.status}
            statusHistory={selectedTask.statusHistory || []}
          />
        </TabPanel>

        {/* Tab 2: History (NEW) */}
        <TabPanel value={2} sx={{ mt: 2 }}>
          <StatusAuditTrail taskId={selectedTask.id} limit={100} />
        </TabPanel>

        {/* Tab 3: Errors (NEW) */}
        <TabPanel value={3} sx={{ mt: 2 }}>
          <ValidationFailureUI taskId={selectedTask.id} limit={50} />
        </TabPanel>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};
```

**Deliverable:** TaskDetailModal with history tabs

**Testing:**

- [ ] Tabs switch correctly
- [ ] Overview tab works (existing)
- [ ] Timeline shows correctly
- [ ] History loads and displays
- [ ] Errors display if any

---

### TASK 6: Update TaskManagement Dashboard (Week 2, Day 2-3)

**Objective:** Add metrics dashboard to main task view

**Changes:**

```javascript
// In TaskManagement.jsx

import { StatusDashboardMetrics } from '../components/tasks/StatusComponents';

function TaskManagement() {
  // ... existing code ...

  return (
    <div className="task-management-container">
      <div className="dashboard-header">
        <h1>Task Management</h1>
      </div>

      {/* NEW: Metrics Dashboard */}
      <div className="metrics-section">
        <StatusDashboardMetrics
          statusHistory={filteredTasks.flatMap((t) => t.statusHistory || [])}
        />
      </div>

      {/* EXISTING: Summary Stats */}
      <div className="summary-stats">{/* ... existing code ... */}</div>

      {/* EXISTING: Tasks Table */}
      <div className="tasks-table-container">{/* ... existing code ... */}</div>
    </div>
  );
}
```

**Styling:** Add to `TaskManagement.css`

```css
.metrics-section {
  margin-bottom: 30px;
  padding: 20px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.metrics-section h2 {
  margin-top: 0;
}
```

**Deliverable:** Metrics dashboard in TaskManagement

**Testing:**

- [ ] Dashboard displays
- [ ] Metrics calculate correctly
- [ ] Time range filtering works
- [ ] Progress bars show
- [ ] Layout is responsive

---

### TASK 7: Create Backward Compatibility Tests (Week 2, Day 3)

**Objective:** Ensure old and new systems work together

**File:** `web/oversight-hub/src/__tests__/integration-status.test.jsx`

```javascript
import { unifiedStatusService } from '../services/unifiedStatusService';

describe('Unified Status Service', () => {
  it('should handle old status values', async () => {
    const result = await unifiedStatusService.updateStatus(
      'task-123',
      'approved',
      { reason: 'Test approval' }
    );
    expect(result.success).toBe(true);
  });

  it('should handle new status values', async () => {
    const result = await unifiedStatusService.updateStatus(
      'task-123',
      'awaiting_approval',
      { reason: 'Test transition' }
    );
    expect(result.success).toBe(true);
  });

  it('should retrieve full history', async () => {
    const history = await unifiedStatusService.getHistory('task-123');
    expect(history.history).toBeDefined();
    expect(Array.isArray(history.history)).toBe(true);
  });

  it('should capture validation failures', async () => {
    const failures = await unifiedStatusService.getFailures('task-123');
    expect(failures.failures).toBeDefined();
  });

  it('should work with TaskDetailModal', () => {
    // Integration test
  });

  it('should work with OrchestratorPage', () => {
    // Integration test
  });
});
```

**Deliverable:** Comprehensive test suite

**Testing:**

- [ ] All tests pass
- [ ] Integration scenarios work
- [ ] No breaking changes
- [ ] Backward compatibility confirmed

---

### TASK 8: User Acceptance Testing (Week 2, Day 4)

**Objective:** Verify entire workflow with real users

**Test Scenarios:**

1. **Approval Workflow**
   - [ ] Create task
   - [ ] View in OrchestratorPage
   - [ ] Click Approve
   - [ ] Confirm dialog
   - [ ] Task approved with history ✓

2. **Task Detail View**
   - [ ] Open TaskDetailModal
   - [ ] Click Timeline tab
   - [ ] See status progression
   - [ ] Click History tab
   - [ ] See full audit trail
   - [ ] Click Errors tab (if any)
   - [ ] See validation failures

3. **Dashboard Metrics**
   - [ ] View TaskManagement
   - [ ] See metrics cards
   - [ ] Filter by time range
   - [ ] Verify calculations

4. **Error Handling**
   - [ ] Try invalid transition
   - [ ] See validation error
   - [ ] Error appears in Failures tab
   - [ ] Recommendation shown

**Deliverable:** UAT sign-off

---

## Implementation Schedule

### Week 1

| Day | Task                     | Owner    | Status |
| --- | ------------------------ | -------- | ------ |
| Mon | Status mapping           | Backend  | 🔄     |
| Tue | Compatibility layer      | Backend  | 🔄     |
| Wed | Unified service          | Frontend | 🔄     |
| Wed | OrchestratorPage updates | Frontend | 🔄     |
| Thu | TaskActions enhancement  | Frontend | 🔄     |
| Fri | Testing & fixes          | QA       | 🔄     |

### Week 2

| Day | Task                         | Owner    | Status |
| --- | ---------------------------- | -------- | ------ |
| Mon | TaskDetailModal tabs         | Frontend | ⏳     |
| Mon | StatusComponents integration | Frontend | ⏳     |
| Tue | TaskManagement metrics       | Frontend | ⏳     |
| Tue | CSS styling                  | Frontend | ⏳     |
| Wed | Backward compatibility tests | QA       | ⏳     |
| Wed | Integration tests            | QA       | ⏳     |
| Thu | User acceptance testing      | Product  | ⏳     |
| Fri | Deployment prep              | DevOps   | ⏳     |

---

## Files to Create/Modify

### New Files

- [ ] `src/services/unifiedStatusService.js` (New)
- [ ] `src/Constants/statusEnums.js` (New)
- [ ] `src/__tests__/integration-status.test.jsx` (New)
- [ ] `docs/phase-6-integration-guide.md` (New)

### Modified Files

- [ ] `src/pages/OrchestratorPage.jsx` (Update)
- [ ] `src/components/tasks/TaskActions.jsx` (Update)
- [ ] `src/components/tasks/TaskDetailModal.jsx` (Update)
- [ ] `src/routes/TaskManagement.jsx` (Update)
- [ ] `src/routes/TaskManagement.css` (Update)

### Backend Files

- [ ] Migration script for status mapping
- [ ] Status compatibility layer
- [ ] Update API documentation

---

## Risk Mitigation

### Risk 1: Breaking Existing Workflows

**Mitigation:**

- Run systems in parallel for 1 week
- Comprehensive backward compatibility tests
- Gradual rollout to users

### Risk 2: Data Loss

**Mitigation:**

- Database backup before migration
- Dry run on staging environment
- Rollback plan ready

### Risk 3: Performance Issues

**Mitigation:**

- Database query optimization
- API response caching
- Load testing before deployment

### Risk 4: User Confusion

**Mitigation:**

- UI documentation updated
- User training session
- Gradual feature rollout
- Support team briefing

---

## Success Criteria

- ✅ All tests passing
- ✅ No breaking changes to existing workflows
- ✅ Approval dialogs enhanced with validation
- ✅ History visible in TaskDetailModal
- ✅ Metrics dashboard functional
- ✅ Error tracking working
- ✅ User acceptance testing passed
- ✅ Documentation updated
- ✅ Team trained on new system
- ✅ Performance acceptable (<500ms response times)

---

## Post-Integration (Future Phases)

### Phase 7: Cleanup (Optional)

- Remove old approval endpoints
- Consolidate API routes
- Archive old code

### Phase 8: Optimization

- WebSocket real-time updates
- Advanced search/filtering
- Archive policies

---

## Sign-Off

**Plan Prepared By:** AI Assistant  
**Approved By:** [Pending]  
**Start Date:** [To be scheduled]  
**Target Completion:** 2-3 weeks

---

**Status:** 📋 Ready for Implementation  
**Next Step:** Schedule kick-off meeting and assign owners
