# Oversight Hub User Testing & Performance Validation Guide

**Date:** March 7, 2026  
**System:** Glad Labs v3.0.2  
**Services:** Backend (8000) + Oversight Hub (3001)

## ✅ Prerequisites Check

Both services are currently running:

- ✅ Backend API: <http://localhost:8000/health> (status: ok)
- ✅ Oversight Hub: <http://localhost:3001> (React dev server active)

---

## 🎯 Testing Objectives

1. **Functional Testing:** Verify all UI features work as expected
2. **Performance Validation:** Measure response times and resource usage
3. **User Experience:** Evaluate usability and workflow efficiency
4. **Integration Testing:** Validate frontend-backend communication

---

## 📋 Testing Scenarios

### Scenario 1: Authentication & Navigation (5 min)

**Purpose:** Verify auth flow and navigation structure

**Steps:**

1. Open <http://localhost:3001> in Chrome
2. ✅ Dev mode auto-auth should redirect to dashboard (not login)
3. ✅ Header shows "🎛️ Oversight Hub"
4. Click hamburger menu icon (top-left)
5. ✅ Verify all 9 nav items visible:
   - Dashboard
   - Tasks
   - Content
   - Approvals
   - Services
   - AI Studio
   - Costs
   - Performance
   - Settings
6. Navigate through each section
7. ✅ No console errors in DevTools

**Performance Metrics:**

- Page load time: Target < 2s
- Navigation transitions: Target < 500ms
- Memory usage: Target < 150MB

---

### Scenario 2: Executive Dashboard Evaluation (5 min)

**Purpose:** Validate KPI display and data visualization

**Steps:**

1. Navigate to <http://localhost:3001/>
2. ✅ "Executive Dashboard" heading visible
3. ✅ 4 KPI cards render:
   - Revenue card
   - Content Published card
   - Tasks Completed card
   - AI Savings card
4. ✅ Time range selector present (Last 7 days, 30 days, etc.)
5. Change time range → KPI values update
6. ✅ Charts/graphs render without errors
7. Check browser console for errors

**Performance Metrics:**

- KPI load time: Target < 1.5s
- Chart render time: Target < 800ms
- API call latency: Target < 500ms

**Test Data:**

- Open DevTools → Network tab
- Filter by XHR/Fetch
- Look for `/api/analytics` or `/api/metrics` calls
- Verify response times

---

### Scenario 3: Task Management Workflow (10 min)

**Purpose:** Test task creation, viewing, and status updates

**Steps:**

1. Navigate to <http://localhost:3001/tasks>
2. ✅ Task list loads (or shows "No tasks" state)
3. Click "Create Task" button
4. Fill in task form:
   - Task type: Blog post
   - Topic: "AI Testing Best Practices"
   - Additional parameters as needed
5. Submit task
6. ✅ Task appears in list with "pending" status
7. Monitor task status changes (pending → running → completed)
8. Click task to view details
9. ✅ Task metadata displays correctly
10. Test filtering by status (pending, completed, failed)
11. Test search functionality

**Performance Metrics:**

- Task creation API: Target < 2s
- Task list render: Target < 1s
- Real-time updates: Target < 3s polling interval
- Filter/search response: Target < 200ms

**Known Issues to Watch:**

- Task status may not auto-refresh (need manual refresh)
- Long-running tasks may timeout (check logs)

---

### Scenario 4: Approval Queue Testing (8 min)

**Purpose:** Validate human-in-the-loop approval workflow

**Steps:**

1. Navigate to <http://localhost:3001/approvals>
2. ✅ Approval queue renders
3. If queue is empty:
   - Create content task from Tasks page
   - Wait for task to enter approval state
4. When approval item appears:
   - Click "View Details"
   - ✅ Content preview displays
   - ✅ Approve/Reject buttons functional
5. Click "Approve"
6. ✅ Item moves to approved state
7. ✅ Task status updates in backend

**Performance Metrics:**

- Approval list load: Target < 1s
- Approve/reject action: Target < 1s
- State synchronization: Target < 2s

---

### Scenario 5: Model Selection & Configuration (5 min)

**Purpose:** Test AI model configuration interface

**Steps:**

1. Navigate to <http://localhost:3001/services>
2. ✅ Available models list displayed
3. ✅ Model status indicators (Ollama, Claude, GPT, Gemini)
4. Change default model selection
5. ✅ Save settings
6. Create test task using new model
7. ✅ Verify task uses selected model

**Performance Metrics:**

- Model list load: Target < 800ms
- Settings save: Target < 500ms
- Model health check: Target < 1s

---

### Scenario 6: Workflow Builder (Advanced) (10 min)

**Purpose:** Test custom workflow creation

**Steps:**

1. Navigate to AI Studio or Workflow Builder
2. ✅ Canvas/builder UI loads
3. Create simple workflow:
   - Research phase → Content phase → Review phase
4. ✅ Phase connectors work
5. Save workflow with name "Test Workflow"
6. Execute workflow with test input
7. Monitor execution progress
8. ✅ Real-time progress updates via WebSocket
9. View execution results

**Performance Metrics:**

- Builder load: Target < 1.5s
- Workflow save: Target < 1s
- Execution start: Target < 2s
- Real-time updates: Target < 500ms latency

---

### Scenario 7: Cost Analytics (5 min)

**Purpose:** Validate cost tracking and reporting

**Steps:**

1. Navigate to <http://localhost:3001/costs>
2. ✅ Cost breakdown displays
3. ✅ Charts show API usage by provider
4. ✅ Model cost comparison visible
5. Filter by date range
6. Export cost report (if available)

**Performance Metrics:**

- Cost dashboard load: Target < 1.5s
- Chart render: Target < 800ms
- Report generation: Target < 3s

---

### Scenario 8: Performance Monitoring (5 min)

**Purpose:** Validate system health monitoring

**Steps:**

1. Navigate to <http://localhost:3001/performance>
2. ✅ System metrics displayed:
   - API response times
   - Database query performance
   - Memory usage
   - Active connections
3. ✅ Real-time updates (if implemented)
4. Check historical metrics

**Performance Metrics:**

- Metrics dashboard load: Target < 1s
- Refresh interval: Target 5-10s
- Data accuracy: Compare with backend logs

---

## 🔬 Automated Testing

### Run Playwright Test Suite

```bash
# From project root
npx playwright test --config playwright.oversight.config.ts

# With UI mode (recommended for debugging)
npx playwright test --config playwright.oversight.config.ts --ui

# Generate HTML report
npx playwright show-report oversight-report
```

**Expected Results:**

- All tests pass (green checkmarks)
- Screenshots captured in `test-results/screenshots/`
- HTML report generated in `oversight-report/`

### Performance Profiling

```bash
# Run with Chrome DevTools performance recording
npx playwright test --config playwright.oversight.config.ts --headed --debug
```

**Lighthouse Performance Audit:**

1. Open Chrome DevTools (F12)
2. Navigate to Lighthouse tab
3. Select "Performance" + "Desktop"
4. Click "Analyze page load"
5. Target scores:
   - Performance: > 90
   - Accessibility: > 90
   - Best Practices: > 90
   - SEO: > 80

---

## 📊 Performance Benchmarks

### API Endpoints (Backend)

| Endpoint           | Target  | Acceptable | Critical |
| ------------------ | ------- | ---------- | -------- |
| GET /health        | < 50ms  | < 100ms    | < 200ms  |
| GET /api/tasks     | < 500ms | < 1s       | < 2s     |
| POST /api/tasks    | < 1s    | < 2s       | < 5s     |
| GET /api/analytics | < 800ms | < 1.5s     | < 3s     |
| WebSocket connect  | < 200ms | < 500ms    | < 1s     |

### Frontend Metrics

| Metric                   | Target | Acceptable | Critical |
| ------------------------ | ------ | ---------- | -------- |
| First Contentful Paint   | < 1s   | < 2s       | < 3s     |
| Largest Contentful Paint | < 1.5s | < 2.5s     | < 4s     |
| Time to Interactive      | < 2s   | < 3.5s     | < 5s     |
| Cumulative Layout Shift  | < 0.1  | < 0.25     | < 0.5    |

---

## 🐛 Common Issues & Troubleshooting

### Issue: "Cannot reach backend API"

**Solution:**

```bash
# Check backend is running
curl http://localhost:8000/health

# If not running, start it
npm run dev:cofounder
```

### Issue: "Blank page or white screen"

**Solution:**

1. Open DevTools console (F12)
2. Look for JavaScript errors
3. Common causes:
   - Missing environment variables
   - CORS errors (should be configured)
   - React compile errors

### Issue: "Authentication loop"

**Solution:**

- Dev mode should auto-authenticate
- Check localStorage in DevTools → Application tab
- Verify `auth_token` and `user` keys present
- Clear localStorage and refresh if needed

### Issue: "Task not updating"

**Solution:**

- Real-time updates use polling or WebSocket
- Check Network tab for `/api/tasks` or WebSocket connection
- Manual refresh may be needed
- Verify task_executor is running in backend logs

### Issue: "Slow performance"

**Solution:**

```bash
# Check Chrome Task Manager (Shift + Esc)
# Look for high CPU/memory usage
# Check backend logs for slow queries

# Enable SQL debug logging
# In .env.local: SQL_DEBUG=true
```

---

## ✅ Testing Checklist

### Functional Tests

- [ ] Authentication works (dev mode)
- [ ] All 9 navigation items accessible
- [ ] Dashboard KPIs load and display
- [ ] Task creation completes successfully
- [ ] Task list displays correctly
- [ ] Task status updates (manual or auto)
- [ ] Approval queue functional
- [ ] Model selection saves
- [ ] Workflow builder loads
- [ ] Cost analytics displays
- [ ] Performance metrics visible

### Performance Tests

- [ ] Page load < 2s
- [ ] API calls < 1s
- [ ] No memory leaks (monitor for 5+ min)
- [ ] Real-time updates work
- [ ] Charts/graphs render smoothly
- [ ] No console errors

### Integration Tests

- [ ] Frontend-backend communication works
- [ ] WebSocket connections stable
- [ ] Database queries efficient
- [ ] Error handling displays user-friendly messages

### Browser Compatibility

- [ ] Chrome (primary)
- [ ] Firefox (secondary)
- [ ] Edge (optional)

---

## 📈 Performance Monitoring Tools

### Browser DevTools

1. **Performance tab:** Record page interactions, analyze flame graphs
2. **Network tab:** Monitor API calls, payload sizes, timing
3. **Memory tab:** Check for memory leaks over time
4. **Console:** Watch for errors and warnings

### Backend Monitoring

```bash
# Watch backend logs
npm run dev:cofounder

# Monitor database queries (if SQL_DEBUG=true)
tail -f src/cofounder_agent/logs/cofounder_agent.log

# Check system resources
# Task Manager (Windows) or Activity Monitor (Mac)
```

### Playwright Trace Viewer

```bash
# Enable trace in config, then view
npx playwright show-trace test-results/trace.zip
```

---

## 📝 Test Report Template

### User Testing Session Report

**Date:** March 7, 2026  
**Tester:** [Your name]  
**Duration:** [Time spent]  
**Browser:** Chrome 122

#### Scenarios Completed

- [ ] Auth & Navigation (5 min)
- [ ] Executive Dashboard (5 min)
- [ ] Task Management (10 min)
- [ ] Approval Queue (8 min)
- [ ] Model Configuration (5 min)
- [ ] Workflow Builder (10 min)
- [ ] Cost Analytics (5 min)
- [ ] Performance Monitoring (5 min)

#### Issues Found

| Severity | Description | Steps to Reproduce | Screenshot |
| -------- | ----------- | ------------------ | ---------- |
| High     | [example]   | [steps]            | [path]     |
| Medium   | [example]   | [steps]            | [path]     |
| Low      | [example]   | [steps]            | [path]     |

#### Performance Metrics Observed

- Average page load: [X]s
- Average API response: [X]ms
- Memory usage after 10 min: [X]MB
- Console errors: [X] count

#### Recommendations

1. [Recommendation 1]
2. [Recommendation 2]
3. [Recommendation 3]

---

## 🚀 Next Steps

1. **Run automated tests:** `npx playwright test --config playwright.oversight.config.ts`
2. **Perform manual testing:** Follow scenarios 1-8 above
3. **Document issues:** Create GitHub issues for bugs found
4. **Generate report:** Use template above
5. **Share findings:** Discuss with team

**Questions?** Check docs/ directory or open an issue.
