# Phase 1: Integration Testing - Chat, Agents, Workflow Pages

**Date:** December 8, 2024  
**Status:** Ready for Testing  
**Scope:** Integration testing of 3 new Oversight Hub UI pages with FastAPI backend

---

## Executive Summary

This document provides step-by-step instructions for testing the 3 new Oversight Hub UI pages:
1. **ChatPage.jsx** - AI chat interface with multi-model support
2. **AgentsPage.jsx** - Multi-agent monitoring and control
3. **WorkflowHistoryPage.jsx** - Workflow execution tracking

Each page is designed with **graceful fallback to mock data**, so they work even if the backend is unavailable.

---

## Architecture Overview

### Pages Created
```
web/oversight-hub/src/components/pages/
├── ChatPage.jsx              (364 lines) - Chat interface
├── ChatPage.css              (525 lines) - Chat styling
├── AgentsPage.jsx            (404 lines) - Agent monitoring
├── AgentsPage.css            (581 lines) - Agent styling
├── WorkflowHistoryPage.jsx   (404 lines) - Workflow tracking
└── WorkflowHistoryPage.css   (496 lines) - Workflow styling
```

### API Client Methods Added

#### Chat Methods (4)
- `sendChatMessage(message, model, conversationId)` → POST `/api/chat`
- `getChatHistory(conversationId)` → GET `/api/chat/history/{id}`
- `clearChatHistory(conversationId)` → DELETE `/api/chat/history/{id}`
- `getAvailableModels()` → GET `/api/chat/models`

#### Agent Methods (4)
- `getAgentStatus(agentId)` → GET `/api/agents/{id}/status`
- `getAgentLogs(agentId, limit)` → GET `/api/agents/{id}/logs`
- `sendAgentCommand(agentId, command)` → POST `/api/agents/{id}/command`
- `getAgentMetrics(agentId)` → GET `/api/agents/{id}/metrics`

#### Workflow Methods (5)
- `getWorkflowHistory(limit, offset)` → GET `/api/workflow/history`
- `getExecutionDetails(executionId)` → GET `/api/workflow/execution/{id}`
- `retryExecution(executionId)` → POST `/api/workflow/execution/{id}/retry`
- `getDetailedMetrics(timeRange)` → GET `/api/metrics/detailed`
- `exportMetrics(format, timeRange)` → GET `/api/metrics/export`

---

## Testing Phases

### Phase 1A: Offline Testing (Mock Data)
**Duration:** 15 minutes  
**Objective:** Verify UI functionality without backend

**Steps:**
1. Ensure backend is NOT running
2. Navigate to Chat page in Oversight Hub
3. Verify:
   - Page loads without errors
   - Model selector works
   - Message input accepts text
   - Chat displays mock messages
   - Clear history works
4. Navigate to Agents page
   - Verify 5 agents load (Content, Financial, Market, Compliance, Orchestrator)
   - Try to select different agents
   - Try sending commands (will use mock response)
5. Navigate to Workflow History page
   - Verify 5 mock executions load
   - Test filtering by status
   - Test sorting
   - Test search
   - Expand execution cards

**Expected Result:** All pages functional with mock data, no console errors

---

### Phase 1B: Online Testing (Real Backend)
**Duration:** 30-45 minutes  
**Prerequisites:** Backend running at http://localhost:8000

**Setup:**
```bash
# Terminal 1 - Start backend
cd src/cofounder_agent
python main.py

# Terminal 2 - Start frontend
cd web/oversight-hub
npm start
```

**Chat Page Testing:**

1. **Send message test**
   - Type: "Hello, what can you do?"
   - Expected: Get response from selected model
   - Check backend logs for:
     - Request: POST /api/chat
     - Response: { response: "...", model: "...", conversationId: "..." }

2. **Model selection test**
   - Try each model (OpenAI GPT-4, Claude, Gemini, Ollama)
   - Expected: Each should work (or gracefully fail with mock)
   - Note: Some models may not be available (API keys missing)

3. **Conversation persistence**
   - Send 3 messages in sequence
   - Expected: Full conversation history visible
   - Check localStorage for conversationId persistence

4. **Clear history test**
   - Click "Clear History" button
   - Expected: Chat resets to initial state
   - Check backend confirmation

5. **Error handling**
   - Disconnect network → try sending message
   - Expected: Error message displayed, mock response offered
   - Expected: UI remains functional

**Agent Page Testing:**

1. **Agent loading test**
   - Navigate to Agents page
   - Expected: 5 agents load
   - Monitor network tab:
     - GET /api/agents/content/status
     - GET /api/agents/financial/status
     - etc.

2. **Agent selection test**
   - Click each agent in sidebar
   - Expected: Status and logs update for selected agent
   - Backend calls:
     - GET /api/agents/{id}/status
     - GET /api/agents/{id}/logs

3. **Command execution test**
   - Select an agent
   - Type command: "Analyze market trends"
   - Click "Send Command"
   - Expected: Command logged, agent status updates
   - Backend call: POST /api/agents/{id}/command

4. **Log filtering test**
   - Use log level filter (INFO, DEBUG, WARNING, ERROR)
   - Expected: Only selected level shown

5. **Auto-refresh test**
   - Enable auto-refresh with 5-second interval
   - Expected: Agent status updates automatically
   - Check network tab for periodic GET requests

**Workflow Page Testing:**

1. **History loading test**
   - Navigate to Workflow page
   - Expected: Execution history loads
   - Backend call: GET /api/workflow/history

2. **Filtering test**
   - Filter by status: Completed, Failed, Running
   - Expected: Only matching executions shown
   - No backend call needed (client-side filtering)

3. **Sorting test**
   - Sort by Date, Status, Duration
   - Toggle ascending/descending
   - Expected: Executions reorder correctly

4. **Search test**
   - Search for workflow name or execution ID
   - Expected: Results filtered in real-time

5. **Expansion test**
   - Click execution card to expand
   - Expected: Shows detailed info:
     - Agents involved
     - Tasks completed
     - Output/results
     - Error messages (if any)

6. **Retry test**
   - Find a failed execution
   - Click "Retry" button
   - Expected: Confirmation dialog → confirmation
   - Backend call: POST /api/workflow/execution/{id}/retry
   - New execution appears in history

7. **Export test**
   - Click "Export Metrics"
   - Select format (CSV, JSON, PDF)
   - Expected: File downloads
   - Backend call: GET /api/metrics/export?format=...

---

## Browser DevTools Testing

### Network Tab Monitoring

**Expected requests for Chat page:**
```
POST /api/chat
  Request: { message: "...", model: "...", conversationId: "..." }
  Response: { response: "...", model: "...", timestamp: "..." }
  Status: 200 OK
```

**Expected requests for Agents page:**
```
GET /api/agents/content/status
GET /api/agents/content/logs
GET /api/agents/financial/status
... (for each selected agent)
```

**Expected requests for Workflow page:**
```
GET /api/workflow/history
GET /api/workflow/execution/{id}  (when expanding)
POST /api/workflow/execution/{id}/retry
GET /api/metrics/export?format=csv
```

### Console Tab Monitoring

**Expected logs:**
```javascript
// Chat sending
[Chat] Sending message: { message: "...", model: "...", ... }
[Chat] Response: { response: "...", model: "...", ... }

// Agents
🔵 makeRequest: GET /api/agents/content/status
🟡 makeRequest: Response status: 200 OK
✅ makeRequest: Returning result

// Workflow
🔵 makeRequest: GET /api/workflow/history
🟡 makeRequest: Response status: 200 OK
✅ makeRequest: Returning result
```

**No console errors expected** (only warnings for unavailable APIs)

---

## Fallback Testing

### When Backend APIs are Unavailable

**Expected behavior:**
1. Console shows warning: `"API not available, using mock data"`
2. UI still loads and functions with mock data
3. User can interact fully (send messages, select agents, expand workflows)
4. No error dialogs or broken UI

**Test procedure:**
1. Stop backend: `Ctrl+C` in terminal
2. Refresh page in browser
3. Verify each page loads with mock data
4. Try interactions (message, command, expand) - all should work with mock

**Success criteria:**
- ✅ No console errors
- ✅ UI fully functional
- ✅ Mock data displays properly
- ✅ No 404 errors in network tab
- ✅ Graceful error messages if trying network action

---

## Performance Testing

### Load Time Goals
- **Chat page:** < 2 seconds load
- **Agents page:** < 1 second load  
- **Workflow page:** < 1.5 seconds load (depends on history size)

### API Response Time Goals
- `sendChatMessage`: < 30 seconds (LLM inference time)
- `getAgentStatus`: < 500ms
- `getAgentLogs`: < 500ms
- `getWorkflowHistory`: < 1 second

### Memory Usage
- Monitor DevTools Memory tab
- Expected: < 50MB increase per page
- No memory leaks on rapid navigation

---

## Error Handling Validation

### Test Cases

1. **Network timeout**
   - Simulate slow network (DevTools)
   - Expected: Operation times out with user-friendly message

2. **401 Unauthorized**
   - Send request without valid token
   - Expected: Error message, possible redirect to login

3. **500 Server error**
   - Backend returns 500 error
   - Expected: Error displayed to user, graceful fallback

4. **Invalid model selection**
   - Try non-existent model
   - Expected: Error message, fallback to default

5. **Missing agent ID**
   - Send command to non-existent agent
   - Expected: 404 error handled gracefully

---

## Verification Checklist

### Chat Page
- [ ] Page loads without errors
- [ ] Models dropdown populated
- [ ] Messages send and receive responses
- [ ] Conversation history persists
- [ ] Clear history works
- [ ] Ollama models fetch correctly
- [ ] Error handling works
- [ ] Responsive design works (mobile/tablet)

### Agents Page
- [ ] All 5 agents load
- [ ] Agent selection updates logs/status
- [ ] Commands send successfully
- [ ] Log filtering works (INFO/DEBUG/WARNING/ERROR)
- [ ] Auto-refresh works at various intervals
- [ ] Agent metrics display correctly
- [ ] Error handling works

### Workflow Page
- [ ] History loads correctly
- [ ] Filtering by status works (All/Completed/Failed/Running)
- [ ] Sorting works (Date/Status/Duration)
- [ ] Search works
- [ ] Execution cards expand/collapse
- [ ] Retry button works for failed executions
- [ ] Export metrics works
- [ ] Responsive design works

### Overall
- [ ] No console errors
- [ ] No console warnings about missing APIs
- [ ] Graceful fallback to mock data
- [ ] All forms validate input
- [ ] Loading states display
- [ ] Error states display clearly

---

## Troubleshooting

### "API not available" warnings

**Cause:** Backend endpoints not implemented or not returning expected format

**Solution:**
1. Check backend is running: `http://localhost:8000/docs` (FastAPI Swagger UI)
2. Check endpoint exists in Swagger
3. Check response format matches expected model
4. Check CORS headers are configured (if accessing from different domain)

### Messages not sending in Chat

**Cause:** Model not available or API error

**Solution:**
1. Check DevTools Network tab for 404/500 errors
2. Check backend logs for exceptions
3. Try different model
4. Verify API_BASE_URL in cofounderAgentClient.js

### Agents page shows only mock data

**Cause:** getAgentStatus API not available

**Solution:**
1. Check backend has agents_routes.py
2. Check endpoint: GET /api/agents/{id}/status
3. Verify agent IDs match: content, financial, market, compliance, orchestrator

### Can't expand workflow details

**Cause:** getExecutionDetails API not available or wrong format

**Solution:**
1. Check backend has workflow_history.py
2. Check endpoint: GET /api/workflow/execution/{id}
3. Ensure response includes agents_involved, tasks_completed, output, error_message

---

## Success Criteria

### Minimum Success (Mock Data Only)
- ✅ All 3 pages load without JavaScript errors
- ✅ All interactive elements work (buttons, dropdowns, inputs)
- ✅ Mock data displays properly
- ✅ Navigation between pages works

### Full Success (With Backend APIs)
- ✅ All API endpoints called correctly (verified in Network tab)
- ✅ Real data displays instead of mock data
- ✅ All user interactions work end-to-end
- ✅ Error handling works for various failure scenarios
- ✅ Performance meets goals (< 2s page load, < 1s API response)

---

## Next Steps After Testing

1. **If all tests pass:**
   - Move to Phase 2: Enhanced Feature Pages (Metrics, Content, Social, Ollama)
   - Update backend endpoints if any API contracts need adjustment

2. **If tests reveal issues:**
   - Document failures in test report
   - Fix backend endpoints or UI components as needed
   - Rerun tests

3. **If APIs not available:**
   - Implement missing backend endpoints using gap analysis
   - Ensure response formats match expected Pydantic models
   - Test again

---

## Test Report Template

```markdown
# Phase 1 Integration Test Report

## Date: [Date]
## Tester: [Name]
## Backend Status: [Running/Not Running]

### Chat Page
- Load time: __ seconds
- Mock data working: [Yes/No]
- Real API calls working: [Yes/No]
- Issues found:
  - [Issue 1]
  - [Issue 2]

### Agents Page
- Load time: __ seconds
- Mock data working: [Yes/No]
- Real API calls working: [Yes/No]
- Issues found:
  - [Issue 1]

### Workflow Page
- Load time: __ seconds
- Mock data working: [Yes/No]
- Real API calls working: [Yes/No]
- Issues found:
  - [Issue 1]

### Overall Results
- [ ] All tests passed
- [ ] Some tests failed - see issues above
- [ ] Tests blocked by missing APIs

### Conclusion
[Summary of findings and next steps]
```

---

## Additional Resources

- **Backend API Documentation:** `docs/API_REFERENCE.md`
- **FastAPI Swagger UI:** `http://localhost:8000/docs`
- **Chat Routes:** `src/cofounder_agent/routes/chat_routes.py`
- **Agent Routes:** `src/cofounder_agent/routes/agents_routes.py`
- **Workflow Routes:** `src/cofounder_agent/routes/workflow_history.py`
- **API Client:** `web/oversight-hub/src/services/cofounderAgentClient.js`

