# Blog Workflow System - Debugging & Testing Guide

## 🔧 System Validation & Troubleshooting

### ✅ Component Verification

All required components are verified and in place:

**File Structure:**
- ✅ `web/oversight-hub/src/pages/BlogWorkflowPage.jsx` - UI component
- ✅ `web/oversight-hub/src/lib/apiClient.js` - API client
- ✅ `web/oversight-hub/src/routes/AppRoutes.jsx` - Application routes
- ✅ `web/oversight-hub/src/components/common/Sidebar.jsx` - Navigation sidebar

**Integration Points:**
- ✅ `/workflows` route defined and protected
- ✅ BlogWorkflowPage imported in AppRoutes
- ✅ Workflows link added to Sidebar with 🔄 icon
- ✅ All API endpoints defined in apiClient

**API Endpoints:**
- ✅ getAvailablePhases()
- ✅ executeWorkflow()
- ✅ getWorkflowProgress()
- ✅ getWorkflowResults()
- ✅ listWorkflowExecutions()
- ✅ cancelWorkflowExecution()

---

## 🚀 Starting the Development Environment

### Option 1: Start Everything (Recommended)

```bash
# From project root
npm run dev
```

This starts all three services concurrently:
- Backend FastAPI: http://localhost:8000
- Oversight Hub React: http://localhost:3001 (or next available port)
- Public Site Next.js: http://localhost:3000

### Option 2: Start Services Individually

```bash
# Terminal 1: Backend API
npm run dev:cofounder

# Terminal 2: Oversight Hub Admin
npm run dev:oversight

# Terminal 3: Public Site
npm run dev:public
```

### Option 3: Verify Specific Services

```bash
# Check backend is running
curl http://localhost:8000/health

# Check oversight hub is accessible
curl http://localhost:3001

# Check public site is running
curl http://localhost:3000
```

---

## 🧪 Running Automated Tests

### Run All Tests

```bash
# From project root - runs full test suite
npm test
```

### Run Backend Tests Only

```bash
cd src/cofounder_agent
poetry run pytest test_blog_workflow.py -v
```

**Expected Output:**
```
✓ test_blog_workflow
✓ test_blog_phase_definitions
✓ test_workflow_executor

3 passed in 5.09s
```

### Run Frontend API Tests Only

```bash
cd web/oversight-hub
npm test -- workflowAPI.test.js
```

**Expected Output:**
```
 ✓ src/services/__tests__/workflowAPI.test.js (34 tests)
34 passed in 1.18s
```

### Run Component Tests (Optional)

```bash
cd web/oversight-hub
npm install --save-dev @testing-library/react  # If not installed
npm test -- BlogWorkflowPage.test.jsx
```

---

## 🌐 Testing the UI in Browser

### Quick Verification (5 minutes)

1. **Start dev server:**
   ```bash
   npm run dev
   ```

2. **Wait for servers to start:**
   - Watch console for "ready in X ms" message
   - Note the port (usually 3001, but check if different)

3. **Navigate to Oversight Hub:**
   ```
   http://localhost:3001
   ```
   (or the port shown in console)

4. **Login:**
   - Use test credentials configured in your environment
   - Check `/login` page if not logged in

5. **Navigate to Workflows:**
   - Look for sidebar with items like "Dashboard", "Tasks", etc.
   - Click on "Workflows" link (with 🔄 icon)
   - Should load "Blog Post Workflow Builder" page

6. **Verify UI Loads:**
   - See 4-step stepper: Design → Configure → Execute → Results
   - See phase checkboxes loading
   - See "Next: Configure Parameters" button

### Comprehensive Testing (30 minutes)

Follow the Quick Manual Test Checklist from **QA_DEPLOYMENT_RUNBOOK.md** (5 main steps):

1. **Step 1: Design Workflow** (5 min)
   - Navigate to /workflows
   - See 4 phases displayed
   - Toggle phases on/off
   - Click "Next: Configure Parameters"

2. **Step 2: Configure Parameters** (5 min)
   - Edit blog topic
   - Change content style/tone
   - Set word count
   - Click "Execute Workflow"

3. **Step 3: Monitor Execution** (3-5 min)
   - See execution summary
   - Click "Start Workflow"
   - Watch progress bar advance
   - See phase names in real-time

4. **Step 4: View Results** (2 min)
   - See workflow completion
   - Review phase results table
   - Click "View Post" link
   - Verify post on public site

5. **Step 5: Verify History** (2 min)
   - Scroll to "Recent Workflow Executions"
   - See executed workflows
   - Test "Refresh History"

---

## 🐛 Common Issues & Solutions

### Issue 1: Oversight Hub Won't Load (Blank Page)

**Symptom:** Browser shows blank white page or "Cannot GET /"

**Causes & Solutions:**

1. **Wrong port:**
   - Check console for actual port (may not be 3001 if in use)
   - Try: http://localhost:3002, http://localhost:3003, etc.

2. **Dev server not running:**
   ```bash
   npm run dev:oversight
   # Watch for "ready in X ms" message
   ```

3. **Browser cache issues:**
   - Hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
   - Or open in incognito/private window

4. **Import error in logs:**
   - Check browser console (F12)
   - Should say "ready in X ms" without red errors
   - If error: check file paths

### Issue 2: /workflows Page Shows Error

**Symptom:** "Cannot find module" or "Route not found"

**Solutions:**

1. **Check file exists:**
   ```bash
   ls web/oversight-hub/src/pages/BlogWorkflowPage.jsx
   ```

2. **Check route is registered:**
   ```bash
   grep -n "path=\"/workflows\"" web/oversight-hub/src/routes/AppRoutes.jsx
   ```

3. **Check Sidebar link:**
   ```bash
   grep -n "to=\"/workflows\"" web/oversight-hub/src/components/common/Sidebar.jsx
   ```

4. **Restart dev server:**
   ```bash
   # Stop with Ctrl+C
   npm run dev:oversight
   ```

### Issue 3: API Calls Failing

**Symptom:** Error messages when trying to load phases or execute workflow

**Causes & Solutions:**

1. **Backend not running:**
   ```bash
   npm run dev:cofounder
   # Should see "Application startup complete" message
   ```

2. **Database not accessible:**
   ```bash
   # Check DATABASE_URL is set
   echo $DATABASE_URL
   # Should print postgresql://...
   ```

3. **API endpoint not implemented:**
   ```bash
   # Check backend logs for errors
   # Look for "GET /api/workflows/phases" 404 errors
   ```

4. **Check Network tab:**
   - Open F12 → Network tab
   - Try to load phases
   - Click on API request (e.g., /api/workflows/phases)
   - Check Response tab for error details

### Issue 4: Phases Not Loading

**Symptom:** "Available blog post generation phases:" displays but no checkboxes appear

**Solutions:**

1. **Check API response:**
   ```bash
   curl http://localhost:8000/api/workflows/phases
   # Should return JSON array of phases
   ```

2. **Check phase registration:**
   ```bash
   cd src/cofounder_agent
   poetry run python -c "from services.phase_registry import phase_registry; print(phase_registry.get_phases())"
   ```

3. **Check browser console (F12):**
   - Look for error messages
   - Check Network tab → /api/workflows/phases response

### Issue 5: Workflow Won't Execute

**Symptom:** "Start Workflow" button disabled or clicking does nothing

**Solutions:**

1. **Check topic is not empty:**
   - Topic field should have text
   - Try: "Test Blog Post"

2. **Check at least one phase selected:**
   - Go back to Step 1
   - Verify at least one checkbox is checked

3. **Check backend logs:**
   ```bash
   # Watch backend output for errors
   # Look for exceptions when executing workflow
   ```

4. **Check Network tab:**
   - F12 → Network tab
   - Click "Start Workflow"
   - Check POST /api/workflows/custom request
   - Look at Response for error message

---

## 🔍 Debugging Techniques

### View Browser Console Logs

```
F12 → Console tab
```

Look for:
- Red errors (❌) - problems
- Yellow warnings (⚠️) - might be okay
- Blue logs (ℹ️) - informational

### Monitor Network Requests

```
F12 → Network tab
```

Track API calls:
- Filter by "workflows"
- Watch for failed requests (red)
- Check response status and body

### Check Backend Logs

```bash
# Terminal running: npm run dev:cofounder

# Look for:
# - "INFO: Application startup complete"
# - "GET /api/workflows/phases" requests
# - Any red ERROR messages
```

### Check Database Connection

```bash
# Test database connection
psql $DATABASE_URL -c "SELECT 1;"

# Should output: 1
```

### Test API Endpoints Directly

```bash
# Get available phases
curl http://localhost:8000/api/workflows/phases | jq

# View workflow execution history
curl http://localhost:8000/api/workflows/executions | jq

# Check specific execution
curl http://localhost:8000/api/workflows/executions/{id}/progress | jq
```

---

## ✅ Verification Checklist

Before proceeding with full testing, verify:

- [ ] Backend is running: `npm run dev:cofounder`
  - Console shows "Application startup complete"

- [ ] Oversight Hub is running: `npm run dev:oversight`
  - Console shows "ready in X ms"

- [ ] Can access Oversight Hub: http://localhost:3001
  - Page loads without errors

- [ ] Can access login page: http://localhost:3001/login
  - Login form displays

- [ ] Can login
  - Redirects to dashboard
  - Shows sidebar with navigation items

- [ ] Can navigate to /workflows
  - URL shows http://localhost:3001/workflows
  - Page shows "Blog Post Workflow Builder"

- [ ] Can see phases loading
  - Checkboxes appear for 4 blog phases
  - All phases are checked by default

- [ ] Sidebar shows Workflows link
  - Link has 🔄 icon
  - Click highlights it as active

---

## 🧬 File Structure Reference

For quick navigation:

```
glad-labs-website/
├── src/cofounder_agent/
│   ├── agents/
│   │   ├── blog_content_generator_agent.py
│   │   ├── blog_quality_agent.py
│   │   ├── blog_image_agent.py
│   │   └── blog_publisher_agent.py
│   ├── services/
│   │   ├── phase_registry.py (modified)
│   │   ├── workflow_executor.py (modified)
│   │   └── database_service.py (modified)
│   └── test_blog_workflow.py
│
├── web/oversight-hub/
│   ├── src/
│   │   ├── pages/
│   │   │   └── BlogWorkflowPage.jsx (NEW)
│   │   ├── lib/
│   │   │   └── apiClient.js (modified)
│   │   ├── routes/
│   │   │   └── AppRoutes.jsx (modified)
│   │   ├── components/
│   │   │   ├── common/
│   │   │   │   └── Sidebar.jsx (modified)
│   │   │   └── __tests__/
│   │   │       └── BlogWorkflowPage.test.jsx (NEW)
│   │   └── services/__tests__/
│   │       └── workflowAPI.test.js (NEW)
│
├── TESTING_GUIDE.md (42 manual tests)
├── QA_DEPLOYMENT_RUNBOOK.md (QA procedures)
├── WORKFLOW_UI_GUIDE.md (user guide)
└── PROJECT_COMPLETION_SUMMARY.md (overview)
```

---

## 📞 Getting Help

1. **Check documentation:**
   - TESTING_GUIDE.md - Manual test procedures
   - QA_DEPLOYMENT_RUNBOOK.md - Troubleshooting section
   - WORKFLOW_UI_GUIDE.md - Feature reference

2. **Check logs:**
   - Browser console (F12 → Console)
   - Network tab (F12 → Network)
   - Backend terminal output

3. **Run tests:**
   - Automated tests validate system health
   - Test output shows what's working/broken

4. **Review code:**
   - BlogWorkflowPage.jsx - UI implementation
   - apiClient.js - API integration
   - phase_registry.py - Backend configuration

---

## 🎯 Next Steps

1. ✅ **Verify system is running** - Follow "Starting the Development Environment"
2. ✅ **Run automated tests** - Follow "Running Automated Tests"
3. ✅ **Test UI in browser** - Follow "Testing the UI in Browser"
4. ✅ **Execute manual tests** - See TESTING_GUIDE.md (42 test cases)
5. ✅ **Document findings** - Use QA sign-off template in QA_DEPLOYMENT_RUNBOOK.md

---

**Status:** ✅ System is verified and ready for comprehensive testing
**Last Updated:** February 25, 2025
