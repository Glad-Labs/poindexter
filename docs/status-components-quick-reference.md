# Status Components - Quick Reference

## Quick Start

### 1. Install Components

```bash
# Components are already created in:
# web/oversight-hub/src/components/tasks/
```

### 2. Import Components

```jsx
import {
  StatusAuditTrail,
  StatusTimeline,
  ValidationFailureUI,
  StatusDashboardMetrics,
} from './components/tasks/StatusComponents';
```

### 3. Use in Your App

```jsx
// Show audit trail
<StatusAuditTrail taskId="task-123" limit={50} />

// Show status timeline
<StatusTimeline currentStatus="in_progress" statusHistory={history} />

// Show validation errors
<ValidationFailureUI taskId="task-123" limit={50} />

// Show dashboard metrics
<StatusDashboardMetrics statusHistory={allHistory} />
```

---

## Component Matrix

| Component                  | Purpose          | API Endpoint                 | Props                        | Best For           |
| -------------------------- | ---------------- | ---------------------------- | ---------------------------- | ------------------ |
| **StatusAuditTrail**       | Complete history | GET /status-history          | taskId, limit                | Task detail views  |
| **StatusTimeline**         | Visual flow      | None (local)                 | currentStatus, statusHistory | Status progression |
| **ValidationFailureUI**    | Error display    | GET /status-history/failures | taskId, limit                | Error panels       |
| **StatusDashboardMetrics** | KPI dashboard    | None (local)                 | statusHistory                | Dashboard views    |

---

## Common Patterns

### Pattern 1: Task Detail Modal

```jsx
<TabPanel value={0}>
  <StatusTimeline currentStatus={task.status} statusHistory={task.history} />
</TabPanel>

<TabPanel value={1}>
  <StatusAuditTrail taskId={task.id} />
</TabPanel>

<TabPanel value={2}>
  <ValidationFailureUI taskId={task.id} />
</TabPanel>
```

### Pattern 2: Dashboard

```jsx
<StatusDashboardMetrics statusHistory={allTasks.flatMap((t) => t.history)} />
```

### Pattern 3: Error Sidebar

```jsx
<Drawer anchor="right" open={showErrors}>
  <ValidationFailureUI taskId={selectedTaskId} />
</Drawer>
```

---

## Props Reference

### StatusAuditTrail

```jsx
<StatusAuditTrail
  taskId="string" // Required: Task ID
  limit={50} // Optional: Max entries (default: 50)
/>
```

### StatusTimeline

```jsx
<StatusTimeline
  currentStatus="pending" // Required: Current status
  statusHistory={[]} // Optional: History array
/>
```

### ValidationFailureUI

```jsx
<ValidationFailureUI
  taskId="string" // Required: Task ID
  limit={50} // Optional: Max entries (default: 50)
/>
```

### StatusDashboardMetrics

```jsx
<StatusDashboardMetrics
  statusHistory={[]} // Optional: History array (default: [])
/>
```

---

## Data Structures

### Status History Item

```javascript
{
  id: "uuid",
  task_id: "task-123",
  old_status: "pending",
  new_status: "in_progress",
  reason: "Task started processing",
  timestamp: "2025-01-16T10:00:00Z",
  metadata: { user_id: "user-123", ... }
}
```

### Audit Trail Response

```javascript
{
  task_id: "task-123",
  history_count: 5,
  history: [
    { /* status history items */ }
  ]
}
```

### Failures Response

```javascript
{
  task_id: "task-123",
  failure_count: 2,
  failures: [
    {
      old_status: "pending",
      new_status: "approved",
      reason: "Insufficient permissions",
      timestamp: "2025-01-16T09:00:00Z",
      metadata: { ... }
    }
  ]
}
```

---

## Status Values

```javascript
const STATUSES = [
  'pending', // Waiting to start
  'in_progress', // Currently processing
  'awaiting_approval', // Waiting for approval
  'approved', // Approved
  'published', // Published/Completed
  'failed', // Failed
  'on_hold', // Temporarily paused
  'rejected', // Rejected by reviewer
  'cancelled', // Cancelled
];
```

---

## Styling Customization

### Override Colors

```css
.status-audit-trail {
  --primary: #2196f3;
  --success: #4caf50;
  --error: #f44336;
  --warning: #ff9800;
}
```

### Responsive Breakpoints

```css
/* Mobile (< 480px) */
/* Tablet (480px - 768px) */
/* Desktop (> 768px) */
```

---

## Common Issues & Fixes

| Issue                  | Fix                                         |
| ---------------------- | ------------------------------------------- |
| "Failed to fetch"      | Check backend is running, verify auth token |
| Components not styling | Verify CSS files exist in same directory    |
| Empty audit trail      | Check API returns data in correct format    |
| "Unauthorized" errors  | Verify auth token is valid and not expired  |
| Slow performance       | Add pagination or limit history size        |

---

## API Endpoints

### Get Status History

```
GET /api/tasks/{taskId}/status-history?limit=50

Response:
{
  task_id: "string",
  history_count: number,
  history: [{ id, old_status, new_status, reason, timestamp, metadata }]
}
```

### Get Validation Failures

```
GET /api/tasks/{taskId}/status-history/failures?limit=50

Response:
{
  task_id: "string",
  failure_count: number,
  failures: [{ old_status, new_status, reason, timestamp, metadata }]
}
```

---

## Authentication

All components use this pattern:

```javascript
const token = localStorage.getItem('authToken');
const headers = {
  Authorization: `Bearer ${token}`,
  'Content-Type': 'application/json',
};
```

Set token after login:

```javascript
localStorage.setItem('authToken', 'your-token-here');
```

---

## Component States

### StatusAuditTrail States

- `loading` → Shows spinner
- `error` → Shows error message with retry button
- `empty` → Shows "No history available"
- `normal` → Displays audit trail entries

### ValidationFailureUI States

- `loading` → Shows spinner
- `error` → Shows error message with retry button
- `empty` → Shows "No failures" success message
- `normal` → Displays failure list

---

## Performance Tips

1. **Limit history size** - Use `limit` prop (default: 50)
2. **Memoize components** - Use `React.memo()` for parent wrapper
3. **Lazy load** - Use `React.lazy()` for dashboard view
4. **Pagination** - Implement for large datasets (1000+ items)
5. **Cache results** - Use React Query or SWR for API calls

---

## Testing

### Mock Data

```jsx
const mockHistory = [
  {
    id: '1',
    old_status: 'pending',
    new_status: 'in_progress',
    reason: 'Task started',
    timestamp: '2025-01-16T10:00:00Z',
    metadata: {},
  },
];

// Use in tests
<StatusTimeline currentStatus="in_progress" statusHistory={mockHistory} />;
```

### Mock API

```jsx
// Using jest.mock()
jest.mock('./api', () => ({
  fetchAuditTrail: jest.fn(() =>
    Promise.resolve({
      history: mockHistory,
    })
  ),
}));
```

---

## Deployment Checklist

- [ ] All 4 components imported correctly
- [ ] CSS files loading without errors
- [ ] Auth token properly configured
- [ ] Backend APIs accessible
- [ ] Console shows no errors/warnings
- [ ] Components render with mock data
- [ ] API calls working with real data
- [ ] Responsive design tested on mobile
- [ ] All interactive features working
- [ ] Error states tested

---

## File Locations

```
web/oversight-hub/src/components/tasks/
├── StatusAuditTrail.jsx            ← Main component
├── StatusAuditTrail.css            ← Styles
├── StatusTimeline.jsx              ← Main component
├── StatusTimeline.css              ← Styles
├── ValidationFailureUI.jsx         ← Main component
├── ValidationFailureUI.css         ← Styles
├── StatusDashboardMetrics.jsx      ← Main component
├── StatusDashboardMetrics.css      ← Styles
└── StatusComponents.js             ← Export all
```

---

## Support

For detailed documentation, see:

- **Integration Guide:** `docs/phase-5-frontend-integration.md`
- **Backend API Docs:** `docs/phase-4-rest-api.md`
- **Database Schema:** `docs/phase-2-database.md`

---

**Last Updated:** January 16, 2026  
**Phase:** 5 (Frontend Integration)  
**Status:** ✅ COMPLETE
