# End-to-End UI Testing Checklist

## Complete Oversight Hub → Poindexter Workflow

**Date.** February 13, 2026  
**Test Objective:** Verify full NLP agent workflow from UI input through backend execution to results display  
**Estimated Duration:** 30-45 minutes

---

## 📋 Phase 1: Service Startup & Health Verification

### Step 1.1: Start All Services

```bash
# From repo root, start all services
npm run dev

# This should start:
# ✅ Backend (port 8000) - FastAPI/Poindexter
# ✅ Oversight Hub (port 3001) - React UI
# ✅ Public Site (port 3000) - Next.js
```

**✓ Verification:** Wait for these lines in terminal:

```
Backend: "Application startup complete"
Oversight Hub: "Webpack compiled with X warnings"
Public Site: "ready - started server on 0.0.0.0:3000"
```

### Step 1.2: Verify Backend Health

```bash
# Quick health check (no dependencies)
curl http://localhost:8000/health

# Expected response:
# {"status": "ok", "service": "cofounder-agent"}
```

**✓ Record your result:**

- [ ] Status: OK
- [ ] Time: ___ ms

### Step 1.3: Verify Backend Components

```bash
# Detailed health (tests database, services)
curl http://localhost:8000/api/health | jq '.'

# Expected response:
# {
#   "status": "healthy",
#   "components": {
#     "database": "ok",
#     "orchestrator": "ok"
#   }
# }
```

**✓ Record your result:**

- [ ] Database: _____ (ok/degraded/unavailable)
- [ ] Orchestrator: _____ (ok/degraded)

### Step 1.4: Verify Frontend Connectivity

```bash
# Check Oversight Hub is served
curl -s http://localhost:3001 | head -1

# Expected: <!doctype html>
```

**✓ Record your result:**

- [ ] Oversight Hub: READY ✅

---

## 🧪 Phase 2: Backend API Testing (Command Line)

### Step 2.1: Chat Endpoint Test

```bash
# Test basic chat with Ollama
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the capital of France?",
    "model": "ollama-llama2",
    "conversationId": "e2e-test-1",
    "temperature": 0.7,
    "max_tokens": 100
  }' | jq '.'
```

**✓ Expected Response:**

```json
{
  "response": "Paris is the capital...",
  "model": "ollama-llama2",
  "provider": "ollama",
  "conversationId": "e2e-test-1",
  "timestamp": "2026-02-13T...",
  "tokens_used": 45
}
```

**✓ Record your result:**

- [ ] Response received: YES / NO
- [ ] Response time: ___ seconds
- [ ] Quality: Good / Medium / Poor
- Response preview: _________________________________

### Step 2.2: Multi-turn Conversation Test

```bash
# Turn 1: Ask a question
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello! Tell me about machine learning",
    "model": "ollama-llama2",
    "conversationId": "e2e-multi-1"
  }' | jq '.response' | head -1

# Turn 2: Follow-up (same conversationId)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What are some real-world applications?",
    "model": "ollama-llama2",
    "conversationId": "e2e-multi-1"
  }' | jq '.response' | head -1
```

**✓ Validation:**

- [ ] Turn 1 response received
- [ ] Turn 2 response received (in <15s)
- [ ] Both responses make sense
- [ ] Context appears preserved

### Step 2.3: Model Fallback Test

```bash
# Try requesting an unavailable model
# Should gracefully fall back to available provider
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello",
    "model": "gpt-4",  # Might not have API key
    "conversationId": "e2e-fallback"
  }' | jq '.'
```

**✓ Validation:**

- [ ] Got response (not 500 error)
- [ ] Either used GPT-4 OR fell back to another provider
- [ ] Error message clear if fallback occurred

### Step 2.4: Task Metrics Test

```bash
# Check task metrics
curl http://localhost:8000/api/metrics | jq '.'
```

**✓ Expected Response:**

```json
{
  "total_tasks": 1542,
  "completed_tasks": 1438,
  "failed_tasks": 24,
  "pending_tasks": 80,
  "success_rate": 98.4,
  "average_execution_time_ms": 4521
}
```

**✓ Record your result:**

- [ ] Total tasks: ___
- [ ] Success rate: ___%
- [ ] Avg execution time: ___ ms

---

## 🎨 Phase 3: Frontend UI Testing

### Step 3.1: Access Oversight Hub

**Action:** Open browser to <http://localhost:3001>

**✓ Validation:**

- [ ] Page loads without errors
- [ ] Title shows "Dexter's Lab - AI Co-Founder"
- [ ] Header displays correctly
- [ ] Navigation menu visible
- [ ] No red error boxes

**✓ Screenshots to take:**

- [ ] Homepage/Dashboard
- [ ] Menu navigation open

### Step 3.2: Navigate to Chat/Orchestrator

**Action:** From main page, look for one of these:

- "Orchestrator Page"
- "Natural Language Composer"
- "Chat" tab/section
- "Tasks" section

**✓ Validation:**

- [ ] Navigation works (no 404 errors)
- [ ] Component loads without errors
- [ ] Input fields visible and functional
- [ ] Buttons render correctly

**✓ Screenshots to take:**

- [ ] Orchestrator/Composer page loaded
- [ ] Input field focused

### Step 3.3: Test Natural Language Input (Simple)

**Action:**

1. Find text input field (labeled "request", "message", "query", etc.)
2. Enter request: `Write a short greeting message`
3. Click the submit/send button

**✓ Validation:**

- [ ] Input accepts text
- [ ] Button is clickable
- [ ] Loading indicator appears (spinner, disabled button, etc.)
- [ ] No immediate JavaScript errors in browser console

**✓ Monitor backend logs:**

```bash
# In terminal running backend (npm run dev:cofounder)
# You should see logs like:
# [Chat] Incoming request - model: 'ollama-llama2'...
```

**✓ Record your result:**

- [ ] Backend log entry visible: YES / NO
- [ ] Loading time: ___ seconds
- [ ] Response appeared in UI: YES / NO

### Step 3.4: Verify Response Display

**Action:** Once task completes, examine the response

**✓ Validation:**

- [ ] Response text visible and readable
- [ ] Response content is relevant to request
- [ ] No truncation or corruption of text
- [ ] Quality metrics visible (if applicable)
- [ ] Timestamp showing when generated

**✓ Record your result:**

- [ ] Response visible: YES / NO
- [ ] Content quality: Good / Medium / Poor
- [ ] Quality score (if shown): _____ / 1.0

**✓ Screenshot:**

- [ ] Response displayed in UI

### Step 3.5: Test Complex Natural Language Request

**Action:**

1. Clear previous input
2. Enter request: `Write a professional LinkedIn post about AI innovation with 3 key points`
3. Submit

**✓ Expected behavior:**

- Request should route to Content Pipeline
- System should compose task with parameters
- Execution should take 20-60 seconds
- Result should be a formatted LinkedIn post

**✓ Validation:**

- [ ] Request submitted successfully
- [ ] Progress indicator shown (if applicable)
- [ ] Status updates visible (in real-time)
- [ ] Final result contains all 3 points
- [ ] Tone is professional
- [ ] No truncation

**✓ Record your result:**

- [ ] Submission: SUCCESS / FAILED
- [ ] Execution time: ___ seconds
- [ ] Content length: ___ words
- [ ] Quality assessment: Good / Medium / Poor

---

## 🔄 Phase 4: Multi-step Workflow Testing

### Step 4.1: Compose → Review → Execute

**Action:**

1. Go to "Natural Language Composer" (if separate from chat)
2. Enter: `Create a Twitter thread about the future of remote work`
3. Click "Compose Task"

**✓ Expected behavior:**

- Task should be analyzed and parameters extracted
- Suggested task shown with details
- Options to review or execute immediately

**✓ Validation:**

- [ ] Composition succeeds
- [ ] Parameters extracted (topic, content_type, tone)
- [ ] Task preview shows options
- [ ] Estimated duration reasonable

**✓ Record your result:**

- [ ] Task composed: YES / NO
- [ ] Parameters visible: YES / NO
- [ ] Estimated time: ___ seconds

### Step 4.2: Execute Composed Task

**Action:** Click "Execute" button on composed task

**✓ Expected behavior:**

- Task execution begins
- Progress indicator shows (% complete or spinning)
- Real-time updates (every 2-5 seconds)
- Form disabled while executing

**✓ Validation:**

- [ ] Execution starts immediately
- [ ] Progress updates visible
- [ ] Status messages clear ("Generating...", "Evaluating...", etc.)
- [ ] No freezing or unresponsiveness

**✓ Monitor:**

- [ ] Watch backend logs for [Orchestrator] entries
- [ ] Watch for [Content] pipeline stages
- [ ] Note any warnings or errors

**✓ Record your result:**

- [ ] Execution started: YES / NO
- [ ] Progress updates: YES / NO
- [ ] Total execution time: ___ seconds
- [ ] Final status: COMPLETED / FAILED

### Step 4.3: Review Generated Content

**Action:** Once execution completes, examine results

**✓ Expected output:**

- Generated Twitter thread (multiple tweets)
- Quality assessment/score
- Metadata (token usage, cost, time)

**✓ Validation:**

- [ ] Content is relevant to request
- [ ] For Twitter thread: 3-5 connected tweets
- [ ] Each tweet is reasonable length (<280 chars)
- [ ] Thread flows logically
- [ ] Tone is consistent
- [ ] Professional quality

**✓ Record your result:**

- [ ] Content present: YES / NO
- [ ] Quality score: _____ / 1.0
- [ ] Tweet count: ___
- [ ] Overall quality: Excellent / Good / Medium / Poor

---

## 📊 Phase 5: Error Handling & Edge Cases

### Step 5.1: Invalid Input Test

**Action:**

1. Enter invalid/nonsensical request: `asdfjkl; 123 xyz @@@@`
2. Submit

**✓ Expected behavior:**

- Should handle gracefully (not crash)
- User-friendly error message
- Suggestion to try again

**✓ Validation:**

- [ ] No 500 error (blue screen crash)
- [ ] Error message is helpful
- [ ] User can retry

### Step 5.2: Empty Input Test

**Action:**

1. Try to submit with empty field
2. Or submit just whitespace

**✓ Expected behavior:**

- Client-side validation prevents submission
- Error message: "Please enter a request"

**✓ Validation:**

- [ ] Form doesn't submit empty
- [ ] Error message appears
- [ ] Button remains enabled for retry

### Step 5.3: Very Long Input Test

**Action:** Paste a very long request (2000+ characters)

**✓ Expected behavior:**

- Should accept or show max length warning
- If accepted, should handle gracefully

**✓ Validation:**

- [ ] No crash or hang
- [ ] Clear feedback (accepted or rejected)

### Step 5.4: Rapid Requests Test

**Action:**

1. Submit a request
2. While loading, try to submit another
3. Check what happens

**✓ Expected behavior:**

- Either: Queue requests (execute sequentially)
- Or: Show "already processing" message
- Or: Cancel previous, start new one

**✓ Validation:**

- [ ] No duplicate executions
- [ ] Clear indication of what will happen
- [ ] UI remains responsive

---

## 🔍 Phase 6: Integration Testing

### Step 6.1: Task History/List View

**Action:** Look for a "History", "Tasks", or "Previous Requests" section

**✓ Validation:**

- [ ] Previous tasks visible
- [ ] Shows task title/request
- [ ] Shows status (completed, failed, pending)
- [ ] Shows timestamp
- [ ] Can click to view details

**✓ Record:**

- [ ] Task list found: YES / NO
- [ ] Number of tasks shown: ___
- [ ] Can view details: YES / NO

### Step 6.2: Task Status Polling

**Action:**

1. Submit a request (should take 20+ seconds)
2. Don't refresh the page
3. Watch the status update in real-time

**✓ Expected behavior:**

- Status updates every 2-5 seconds without page refresh
- No manual refresh needed
- Percentage or stage progression visible

**✓ Validation:**

- [ ] Status updates automatically: YES / NO
- [ ] No page flickering/refresh
- [ ] Updates are smooth and clear

### Step 6.3: Copy/Export Results

**Action:** Look for buttons to copy/download results

**✓ Validation:**

- [ ] Copy button works (text copies to clipboard)
- [ ] Download button works (if present)
- [ ] Exported format is correct

**✓ Record:**

- [ ] Copy/export available: YES / NO
- [ ] Functionality works: YES / NO

---

## 📈 Phase 7: Performance & Load Testing

### Step 7.1: Response Time Analysis

**Action:** Time a few requests end-to-end

| Request Type | Expected | Actual | Status |
|---|---|---|---|
| Chat (simple) | <10s | ___ s | ⬜ |
| Chat (Ollama) | <15s | ___ s | ⬜ |
| Task composition | <3s | ___ s | ⬜ |
| Full pipeline | <120s | ___ s | ⬜ |
| Results display | <1s | ___ s | ⬜ |

### Step 7.2: Browser Performance

**Action:**

1. Open DevTools (F12)
2. Go to Performance tab
3. Submit a request
4. Record performance

**✓ Check:**

- [ ] No JavaScript errors in Console
- [ ] No excessive memory usage
- [ ] Network requests are reasonable count
- [ ] Response times acceptable

**✓ Record:**

- [ ] JavaScript errors: ___
- [ ] Network requests: ___ (for one execution)
- [ ] Memory usage after task: ___ MB
- [ ] Page responsiveness: Good / Medium / Sluggish

### Step 7.3: Network Inspection

**Action:**

1. Open DevTools → Network tab
2. Clear network log
3. Submit a request
4. Examine network requests

**✓ Validation:**

- [ ] All requests return 200/201 (success, not 500)
- [ ] No failed requests (red)
- [ ] Request sizes reasonable
- [ ] Response times acceptable

**✓ Record:**

- [ ] Total requests: ___
- [ ] Failed requests: ___
- [ ] Largest request: ___ KB
- [ ] Slowest request: ___ ms

---

## ✅ Phase 8: Final Verification

### Checklist: All Core Features

| Feature | Working | Notes |
|---------|---------|-------|
| Backend health | ⬜ | |
| Chat endpoint | ⬜ | |
| NLP composition | ⬜ | |
| UI loads without errors | ⬜ | |
| Natural language input | ⬜ | |
| Response display | ⬜ | |
| Multi-step workflow | ⬜ | |
| Error handling | ⬜ | |
| Real-time updates | ⬜ | |
| No console errors | ⬜ | |

### Checklist: Performance Targets

| Metric | Target | Actual | Met |
|--------|--------|--------|-----|
| Backend health check | <50ms | ___ ms | ⬜ |
| Chat response | <10s | ___ s | ⬜ |
| Task composition | <3s | ___ s | ⬜ |
| Full execution | <120s | ___ s | ⬜ |
| UI responsiveness | <100ms | ___ ms | ⬜ |

### Checklist: Quality Standards

| Aspect | Standard | Result | Met |
|--------|----------|--------|-----|
| Content relevance | 100% relevant | ___% | ⬜ |
| No truncation | 0 truncations | ___ found | ⬜ |
| Quality score | >0.7 | _____ | ⬜ |
| No errors/crashes | 0 errors | ___ found | ⬜ |
| Accessibility | Readable | Good / Fair / Poor | ⬜ |

---

## 📝 Test Results Summary

### Overall Assessment

**Date Tested:** ______________  
**Tester:** ______________  
**Duration:** ______________  

**Backend Status:**

- [ ] ✅ All systems operational
- [ ] ⚠️ Some services degraded
- [ ] ❌ Critical failures

**Frontend Status:**

- [ ] ✅ All components working
- [ ] ⚠️ Some UI issues
- [ ] ❌ Major functionality broken

**Integration Status:**

- [ ] ✅ Full E2E workflow working
- [ ] ⚠️ Partial workflow only
- [ ] ❌ Integration broken

### Test Summary Table

| Category | Pass | Fail | Partial | Notes |
|----------|------|------|---------|-------|
| **Backend** | ___ | ___ | ___ | |
| **Frontend** | ___ | ___ | ___ | |
| **Integration** | ___ | ___ | ___ | |
| **Performance** | ___ | ___ | ___ | |
| **Error Handling** | ___ | ___ | ___ | |

### Issues Found

**Critical Issues:**

1. _________________________________
2. _________________________________

**Major Issues:**

1. _________________________________
2. _________________________________

**Minor Issues:**

1. _________________________________
2. _________________________________

### Recommendations

1. _________________________________
2. _________________________________
3. _________________________________

---

## 🔧 Troubleshooting During Testing

### Issue: "Backend not responding"

```bash
# Check if backend is running
curl http://localhost:8000/health

# If not, start it:
npm run dev:cofounder

# Or start all services:
npm run dev
```

### Issue: CORS Error in Browser Console

```javascript
// Error: "Access to XMLHttpRequest blocked by CORS policy"

// Solution:
// 1. Verify REACT_APP_API_URL is set correctly in .env
// 2. Check backend CORS configuration
// 3. Reload page (Ctrl+Shift+R - hard reload)
```

### Issue: Chat Returns Empty Response

```bash
# Check model availability
curl http://localhost:8000/api/models/health

# If Ollama required but not running:
ollama serve &   # Start Ollama in background
ollama pull llama2  # Pull the model

# Then retry your request
```

### Issue: Task Execution Slow or Timing Out

```bash
# Check backend logs for errors
# Look for [Task] or [Orchestrator] entries

# Verify database is responsive
curl http://localhost:8000/api/health

# Check available resources
ps aux | grep python  # See memory usage
```

### Issue: "No auth token" Error

```bash
# If endpoints require authentication:

# Option 1: Get token from API
curl -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"user@example.com","password":"password"}'

# Option 2: Check browser localStorage
# In browser console:
// localStorage.getItem('auth_token')

# Option 3: Use OAuth (GitHub, Google, etc.)
# Click login button in UI
```

---

## 📞 Need Help?

1. **Check Logs:** Watch terminal running `npm run dev:cofounder`
2. **Check Console:** F12 → Console tab in browser
3. **Check Network:** F12 → Network tab, look for failed requests (red)
4. **Review Guides:**
   - `NLP_AGENT_QUICK_REFERENCE.md` - Quick API reference
   - `NLP_AGENT_WORKFLOW_TESTING_GUIDE.md` - Detailed testing guide
   - `FASTAPI_SERVICE_ANALYSIS.md` - Architecture details

---

## 🎉 Success Criteria

You have successfully completed E2E testing when:

1. ✅ Backend health check passes
2. ✅ Chat endpoint responds in <10 seconds
3. ✅ Oversight Hub UI loads without errors
4. ✅ Natural language input accepted and processed
5. ✅ Response generated and displayed in UI
6. ✅ Multi-turn conversation maintains context
7. ✅ Task execution shows progress updates
8. ✅ Final results visible with quality metrics
9. ✅ No JavaScript errors in console
10. ✅ No HTTP 500 errors in network
11. ✅ Performance meets targets (<120s for full pipeline)
12. ✅ Error cases handled gracefully

**When all 12 criteria met:** 🎉 **E2E Testing PASSED** 🎉
