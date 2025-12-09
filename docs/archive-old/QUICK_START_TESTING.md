# Quick Start Guide - Oversight Hub Chat, Agents & Workflow Pages

## Setup

### 1. Install Dependencies (if needed)
```bash
cd web/oversight-hub
npm install
```

### 2. Start Development Server
```bash
npm start
```

Server will start on `http://localhost:3000`

### 3. Verify API Endpoint
The components expect the backend at: `http://localhost:8000`
- Adjust in environment variables if different
- Check network tab to see actual API calls

---

## Navigation to New Pages

### From OversightHub
The main navigation bar now has:
- **💬 Chat** (Position 2) - Access multi-model AI chat
- **🤖 Agents** (Position 3) - Monitor specialized agents
- **📈 Workflow** (Position 7) - View execution history

Click any navigation item to load that page.

---

## Testing Each Page

### Chat Page (💬)
**URL:** Click "Chat" in navigation  
**Test Checklist:**
- [ ] Page loads with welcome message
- [ ] Model dropdown shows available models
- [ ] Can type and send messages
- [ ] Messages appear in conversation
- [ ] Toggle between "Conversation" and "Agent Delegation" modes
- [ ] Agent dropdown appears in Agent Delegation mode
- [ ] Typing indicator shows when waiting for response
- [ ] Clear history button works
- [ ] Page is responsive on mobile

**Expected Data Flow:**
1. User types message
2. Message appears in UI immediately
3. Fetch request to `POST /api/chat` with JSON payload
4. Response appears with timestamp
5. Conversation ID persisted for context

---

### Agents Page (🤖)
**URL:** Click "Agents" in navigation  
**Test Checklist:**
- [ ] Page loads with 5 agents listed in sidebar
- [ ] Clicking agent shows details on right
- [ ] Agent status badge displays (running/idle/error)
- [ ] Task completion metrics show
- [ ] Agent logs display with timestamps
- [ ] Log levels color-coded (INFO/DEBUG/WARNING/ERROR)
- [ ] Can send commands to agent
- [ ] Auto-refresh toggle works
- [ ] Refresh interval dropdown enables/disables properly
- [ ] Page is responsive on mobile

**Expected Agent List:**
1. 📝 Content Agent - Content generation
2. 📊 Financial Agent - Business metrics
3. 🔍 Market Insight Agent - Market analysis
4. ✓ Compliance Agent - Legal checks
5. 🧠 Co-Founder Orchestrator - Multi-agent coordination

**Expected Data Flow:**
1. User clicks agent in sidebar
2. Agent details panel populates
3. Fetch requests to:
   - `GET /api/agents/{id}/status`
   - `GET /api/agents/{id}/logs`
4. User sends command via input
5. Fetch to `POST /api/agents/{id}/command`
6. Logs update with new entry

---

### Workflow History Page (📈)
**URL:** Click "Workflow" in navigation  
**Test Checklist:**
- [ ] Page loads with execution list
- [ ] Each execution shows as expandable card
- [ ] Status badges show (completed/running/failed)
- [ ] Duration displays correctly
- [ ] Expand arrow toggles execution details
- [ ] Details show agents, task counts, output
- [ ] Search bar filters by workflow name
- [ ] Status filter dropdown works
- [ ] Sort by date/status/duration works
- [ ] Sort order (asc/desc) toggle works
- [ ] Failed executions show error message
- [ ] Failed executions have "Retry" button
- [ ] Export button visible
- [ ] Page is responsive on mobile

**Expected Execution Details When Expanded:**
- Involved agents list
- Task statistics (✅ completed, ❌ failed)
- Start/end timestamps
- Duration calculation
- Output/result text
- Error message (if failed)
- Action buttons (Retry, Export)

**Expected Data Flow:**
1. Page loads with execution history
2. User searches/filters/sorts
3. Click card to expand
4. User can see details
5. For failed: Click "Retry" → `POST /api/workflow/execution/{id}/retry`

---

## Debugging

### Network Inspector (F12 → Network Tab)
Look for these requests:
- `POST http://localhost:8000/api/chat` - Chat messages
- `GET http://localhost:8000/api/chat/history/{id}` - Chat history
- `GET http://localhost:8000/api/agents/{id}/status` - Agent status
- `GET http://localhost:8000/api/agents/{id}/logs` - Agent logs
- `POST http://localhost:8000/api/agents/{id}/command` - Agent commands
- `GET http://localhost:8000/api/workflow/history` - Workflow history

### Console Errors (F12 → Console)
- Check for missing imports
- Check for undefined variables
- Check for API response errors
- Auth token issues (401 Unauthorized)

### React DevTools
- Component tree shows: OversightHub → [ChatPage/AgentsPage/WorkflowHistoryPage]
- Props passing verified
- State updates tracked

---

## Mock Data Reference

### Chat
- Models: OpenAI GPT-4, Claude Opus, Gemini Pro, Ollama Mistral
- Conversation stored with ID "default"
- Messages persist in component state

### Agents
```javascript
predefinedAgents = [
  { id: 'content', name: '📝 Content Agent', ... },
  { id: 'financial', name: '📊 Financial Agent', ... },
  { id: 'market', name: '🔍 Market Insight Agent', ... },
  { id: 'compliance', name: '✓ Compliance Agent', ... },
  { id: 'orchestrator', name: '🧠 Co-Founder Orchestrator', ... },
]
```

### Workflow
```javascript
mockExecutions = [
  {
    id: 'exec-001',
    workflowName: 'Content Generation Pipeline',
    status: 'completed',
    // ... more fields
  },
  // 4 more execution examples
]
```

---

## Common Issues & Solutions

### Issue: "Cannot find module ChatPage"
**Solution:** Verify file exists at `src/components/pages/ChatPage.jsx`

### Issue: Pages don't load / blank screen
**Solution:** 
1. Check browser console (F12)
2. Check React DevTools component tree
3. Verify OversightHub.jsx has correct import
4. Verify currentPage state is being set

### Issue: API calls fail with 404
**Solution:**
1. Backend service not running
2. Wrong API endpoint (check REACT_APP_API_URL)
3. Backend doesn't have `/api/chat`, `/api/agents`, `/api/workflow` endpoints

### Issue: Styling looks broken
**Solution:**
1. CSS file not loaded - check network tab
2. CSS file path incorrect
3. Browser cache - hard refresh (Ctrl+Shift+R)

### Issue: Auth errors (401)
**Solution:**
1. No auth token in localStorage
2. Token expired - login again
3. API requires `Authorization: Bearer {token}` header
4. Verify getAuthToken() returns valid token

---

## Performance Optimization Notes

1. **First Load:** Lazy loads pages only when clicked
2. **Memory:** Components clean up on unmount
3. **CSS:** Separate files for caching
4. **Scrolling:** Custom scrollbars use GPU acceleration
5. **Animations:** CSS transitions for smooth UX

---

## Browser DevTools Tips

### For Chat Page
- Set breakpoint in `handleSendMessage` to debug message flow
- Watch `chatMessages` state in React DevTools
- Check localStorage for saved model selection

### For Agents Page
- Watch auto-refresh interval (should be 5000ms by default)
- Check `selectedAgent` state
- Monitor log entries as they accumulate

### For Workflow Page
- Track filter state (`filterStatus`, `sortBy`, `sortOrder`)
- Watch `expandedExecutionId` for expand/collapse logic
- Monitor search query filtering

---

## Features Checklist

### Chat ✅
- [x] Multi-model selection
- [x] Conversation mode
- [x] Agent delegation mode
- [x] Message persistence
- [x] Clear history
- [x] Responsive design

### Agents ✅
- [x] Agent list sidebar
- [x] Real-time status
- [x] Command interface
- [x] Log display
- [x] Auto-refresh
- [x] Responsive design

### Workflow ✅
- [x] Execution history
- [x] Expandable details
- [x] Search filtering
- [x] Status filtering
- [x] Sort options
- [x] Retry failed
- [x] Export results
- [x] Responsive design

---

## Next Testing Phase

After manual testing passes:
1. **Backend Integration:** Connect to real `/api/chat`, `/api/agents`, `/api/workflow` endpoints
2. **Performance Testing:** Load test with many executions/logs
3. **Accessibility Testing:** Keyboard navigation, screen readers
4. **Cross-browser Testing:** Edge, Firefox, Safari
5. **Mobile Testing:** iOS Safari, Chrome Mobile
6. **User Testing:** Gather feedback from stakeholders

---

## API Endpoint Reference

All endpoints require `Authorization: Bearer {token}` header (JWT auth).

### Chat Endpoints
```
POST   /api/chat
       Payload: { message, model, conversation_id }
       Response: { response, conversation_id }

GET    /api/chat/history/{conversation_id}
       Response: { messages: [...] }

DELETE /api/chat/history/{conversation_id}
       Response: { status: "cleared" }

GET    /api/chat/models
       Response: { models: [...] }
```

### Agent Endpoints
```
GET    /api/agents/{agent_id}/status
       Response: { status, tasks_completed, current_task, ... }

GET    /api/agents/{agent_id}/logs?limit=100
       Response: [{ timestamp, level, message }, ...]

POST   /api/agents/{agent_id}/command
       Payload: { command }
       Response: { status, result }

GET    /api/agents/{agent_id}/metrics
       Response: { success_rate, avg_response_time, ... }
```

### Workflow Endpoints
```
GET    /api/workflow/history?limit=50&offset=0
       Response: [{ id, workflowName, status, ... }, ...]

GET    /api/workflow/execution/{execution_id}
       Response: { id, status, agents, tasks, output, ... }

POST   /api/workflow/execution/{execution_id}/retry
       Response: { id, status, ... }

GET    /api/metrics/detailed?range=24h
       Response: { executions, agents, tasks, ... }

GET    /api/metrics/export?format=csv&range=24h
       Response: File (CSV/JSON/PDF)
```

---

**Last Updated:** Current Session  
**Status:** Ready for Testing  
**Next:** Deploy to staging environment
