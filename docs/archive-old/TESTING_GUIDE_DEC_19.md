# 🧪 TESTING GUIDE - 3 Implementations to Verify

**Date:** December 19, 2025  
**Status:** Code complete, ready for testing  
**Environment:** Local development (`localhost:3000` & `localhost:8000`)

---

## 🎯 Quick Testing Overview

You have 3 new/fixed features to test. All implementations are complete and syntax-verified.

| Feature | File | Status | Test Time |
|---------|------|--------|-----------|
| Image Source Selection | CreateTaskModal.jsx | ✅ READY | 5 min |
| KPI Analytics Endpoint | metrics_routes.py | ✅ READY | 10 min |
| Workflow History | ExecutionHub.jsx | ✅ READY | 5 min |

**Total testing time:** ~20 minutes

---

## 1️⃣ IMAGE GENERATION SOURCE SELECTION

### What Changed
- User now selects image source: **Pexels**, **SDXL**, or **Both**
- Only selected source loads (fixes unnecessary SDXL loading)

### How to Test

**Test Case A: Pexels Only**
```
1. Open Oversight Hub (http://localhost:3000)
2. Create new task → Image Generation task
3. In form: Select image source = "Pexels Only"
4. Fill description, submit
✅ Expected: Only Pexels API called, SDXL doesn't load
```

**Test Case B: SDXL Only**
```
1. Create new task → Image Generation task
2. In form: Select image source = "SDXL Only"
3. Fill description, submit
✅ Expected: Only SDXL called, Pexels doesn't try
```

**Test Case C: Both (Fallback)**
```
1. Create new task → Image Generation task
2. In form: Select image source = "Both"
3. Fill description, submit
✅ Expected: Pexels tries first, falls back to SDXL if Pexels fails
```

### Code Location
- **File:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`
- **Lines:** 44-87 (imageSource field), 234-246 (conditional flags)
- **What to look for:** Form has "Image Source" dropdown with 3 options

---

## 2️⃣ KPI ANALYTICS ENDPOINT

### What Changed
- New endpoint: `GET /api/metrics/analytics/kpis`
- Executive Dashboard now shows **real KPI data** instead of mock data
- No more 404 errors

### How to Test

**Test A: API Endpoint (curl)**
```bash
# Get your JWT token from browser:
# Open browser DevTools → Console
# Copy: localStorage.getItem('auth_token')

# Test endpoint:
curl -H "Authorization: Bearer {YOUR_TOKEN}" \
  http://localhost:8000/api/metrics/analytics/kpis?range=30days

# Expected response format:
{
  "kpis": {
    "revenue": { "current": X, "previous": Y, "change": Z },
    "contentPublished": { "current": X, "previous": Y, "change": Z },
    "tasksCompleted": { "current": X, "previous": Y, "change": Z },
    "aiSavings": { "current": X, "previous": Y, "change": Z },
    "engagementRate": { "current": X, "previous": Y, "change": Z },
    "agentUptime": { "current": X, "previous": Y, "change": Z }
  },
  "timestamp": "2025-12-19T...",
  "range": "30days"
}
```

**Test B: Browser UI**
```
1. Open Oversight Hub (http://localhost:3000)
2. Navigate to "Executive Dashboard" tab
3. Look at KPI cards
✅ Expected: 
   - Cards should show real numbers (not mock data)
   - No 404 error in console
   - All 6 metrics displayed (Revenue, Content, Tasks, Savings, Engagement, Uptime)
```

**Test C: Different Time Ranges**
```bash
# Test 7 days
curl -H "Authorization: Bearer {TOKEN}" \
  http://localhost:8000/api/metrics/analytics/kpis?range=7days

# Test 90 days
curl -H "Authorization: Bearer {TOKEN}" \
  http://localhost:8000/api/metrics/analytics/kpis?range=90days

# Test all
curl -H "Authorization: Bearer {TOKEN}" \
  http://localhost:8000/api/metrics/analytics/kpis?range=all

✅ Expected: All return valid KPI data
```

### Code Location
- **File:** `src/cofounder_agent/routes/metrics_routes.py`
- **Lines:** 586-746 (161 new lines)
- **Endpoint:** `GET /api/metrics/analytics/kpis`
- **What to look for:** Database queries, period-over-period calculations, proper response format

---

## 3️⃣ WORKFLOW HISTORY INTEGRATION

### What Changed
- ExecutionHub **History tab** now populated with real workflow executions
- Previously showed empty list/mock data
- Auto-refreshes every 10 seconds

### How to Test

**Test A: History Tab Display**
```
1. Open Oversight Hub (http://localhost:3000)
2. Navigate to "Execution Hub" tab
3. Click "History" subtab
✅ Expected:
   - List of workflow executions displays
   - Shows execution IDs, timestamps, statuses
   - Not empty (if you've run any tasks)
```

**Test B: Auto-Refresh**
```
1. Open Execution Hub → History tab
2. Watch the list for 30 seconds
✅ Expected:
   - List updates every 10 seconds
   - New executions appear automatically
   - No page refresh needed
```

**Test C: Click Execution**
```
1. Open Execution Hub → History tab
2. Click on any execution row
✅ Expected:
   - Shows execution details
   - Details include task info, status, timing
   - Or modal opens with execution info
```

**Test D: Verify No Errors**
```
1. Open browser DevTools → Console
2. Go to Execution Hub → History tab
✅ Expected:
   - No error messages in console
   - No failed API calls
   - Network tab shows successful GET to /api/workflow/history
```

### Code Location
- **File:** `web/oversight-hub/src/components/pages/ExecutionHub.jsx`
- **Lines:** 30-75 (~45 lines added)
- **API Endpoints:**
  - `GET /api/workflow/history` (main data)
  - `GET /api/workflow/{id}/details` (detail view)
  - `GET /api/workflow/statistics` (stats)
  - `GET /api/workflow/performance-metrics` (metrics)
- **What to look for:** Fetch calls with JWT auth, error handling, state updates

---

## 📋 TESTING CHECKLIST

### Pre-Testing
- [ ] Both services running:
  - [ ] Frontend: `npm start` in `web/oversight-hub/` (port 3000)
  - [ ] Backend: `python -m uvicorn main:app --reload` (port 8000)
- [ ] Logged in to Oversight Hub
- [ ] Browser DevTools open (console + network tabs)

### Image Generation Testing
- [ ] Test: Pexels only → SDXL doesn't load
- [ ] Test: SDXL only → Pexels doesn't load
- [ ] Test: Both → Both available
- [ ] Verify: Form has image source dropdown

### KPI Endpoint Testing
- [ ] API: curl returns valid JSON
- [ ] API: Different time ranges work
- [ ] Browser: Executive Dashboard loads
- [ ] Browser: KPI cards show real numbers
- [ ] Browser: No 404 errors in console

### Workflow History Testing
- [ ] Browser: History tab shows executions
- [ ] Browser: List auto-refreshes every 10s
- [ ] Browser: Can click execution for details
- [ ] Browser: No console errors
- [ ] Network: GET /api/workflow/history succeeds

### Final Verification
- [ ] No console errors across all tests
- [ ] All API responses have correct format
- [ ] All UI updates display correctly
- [ ] All time ranges work for KPI endpoint
- [ ] Auto-refresh works for workflow history

---

## 🐛 Troubleshooting

### If Image Generation dropdown not visible
- **Check:** CreateTaskModal.jsx lines 44-87
- **Verify:** imageSource field is in form state
- **Fix:** Clear browser cache, refresh page

### If KPI Endpoint returns 404
- **Check:** Backend running on port 8000
- **Verify:** metrics_routes.py lines 586-746 exist
- **Test:** `curl http://localhost:8000/api/metrics/analytics/kpis` (without auth)
- **Fix:** Restart FastAPI server

### If Workflow History shows empty
- **Check:** Backend has workflow data in database
- **Verify:** ExecutionHub.jsx lines 30-75 have fetch call
- **Test:** Create a task first (so there's data)
- **Check:** Console for API errors

### If auto-refresh not working
- **Check:** Browser doesn't have tab in background
- **Verify:** No JavaScript errors in console
- **Check:** Network tab shows 10-second interval requests
- **Fix:** Refresh page, clear cache

---

## 📊 Test Results Template

Copy and fill in after testing:

```markdown
## Testing Results - December 19

**Tester:** [Your name]
**Date:** [Date tested]
**Environment:** Local development

### Image Generation Source Selection
- [ ] Pexels only: PASS / FAIL
  - Notes: 
- [ ] SDXL only: PASS / FAIL
  - Notes:
- [ ] Both: PASS / FAIL
  - Notes:

### KPI Analytics Endpoint
- [ ] curl test: PASS / FAIL
  - Notes:
- [ ] Executive Dashboard: PASS / FAIL
  - Notes:
- [ ] Time ranges: PASS / FAIL
  - Notes:

### Workflow History Integration
- [ ] History tab displays: PASS / FAIL
  - Notes:
- [ ] Auto-refresh works: PASS / FAIL
  - Notes:
- [ ] Click details works: PASS / FAIL
  - Notes:

### Overall
- [ ] No console errors
- [ ] All API calls successful
- [ ] UI looks correct
- [ ] Ready to deploy: YES / NO

**Issues found:**
- [ ] None
- [ ] Minor (cosmetic)
- [ ] Major (functionality broken)

**Details:**
[Describe any issues found]
```

---

## ✅ Testing Success Criteria

All of the following must be true:

✅ **Image Generation:**
- Form has image source dropdown
- "Pexels only" doesn't load SDXL
- "SDXL only" doesn't load Pexels
- "Both" loads both with fallback

✅ **KPI Endpoint:**
- API responds with valid JSON
- Executive Dashboard shows real KPI data
- All 6 metrics display
- No 404 errors
- Different time ranges work

✅ **Workflow History:**
- History tab shows executions
- Data auto-refreshes every 10 seconds
- Can click for details
- No console errors

✅ **Overall:**
- No JavaScript errors in console
- All API calls successful
- All UI elements render correctly
- Performance is acceptable

---

## 📞 If Testing Finds Issues

1. **Check the code:**
   - See file paths and line numbers above
   - Verify implementation matches documented changes

2. **Check the logs:**
   - Browser console for frontend errors
   - FastAPI terminal for backend errors
   - Network tab for API failures

3. **Check dependencies:**
   - Ensure all required packages installed
   - Verify Python venv is activated
   - Check npm dependencies are up to date

4. **Clear cache & restart:**
   - Clear browser cache
   - Restart FastAPI server
   - Restart React dev server
   - Refresh browser

5. **Review the decision record:**
   - See: `docs/decisions/FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md`
   - Contains implementation details and architecture decisions

---

## 🎉 After Testing

Once all tests pass:
1. ✅ Document results using template above
2. ✅ Share with team
3. ✅ Prepare for staging deployment
4. ✅ Plan production deployment

**Status:** All 3 features are production-ready pending successful testing.

---

**Test Duration:** ~20 minutes  
**Difficulty:** Easy (mostly clicking and observing)  
**Risk Level:** Low (no data loss, all changes backward compatible)

Good luck with testing! 🚀
