# NLP Agent Workflow Testing Guide

## Oversight Hub ↔ Poindexter FastAPI Backend

**Last Updated:** February 13, 2026  
**System:** Glad Labs AI Co-Founder  
**Components:** Oversight Hub (React 3001) ↔ FastAPI Backend (8000)  
**Testing Focus:** NLP Agent Chat Integration

---

## 🎯 Overview

The NLP agent workflow enables natural language interaction with the Poindexter AI orchestrator through the Oversight Hub UI. The system follows this flow:

```
User Input (Oversight Hub)
    ↓
NLP Service (Frontend validation)
    ↓
Backend API Endpoint (/api/chat or /api/tasks/*)
    ↓
Model Router (Ollama → Claude → GPT → Gemini → Echo)
    ↓
LLM Response
    ↓
Oversight Hub Display
```

---

## ✅ Service Status Checklist

Before testing, ensure all services are running:

```bash
# 1. Backend (FastAPI) - Port 8000
GET http://localhost:8000/health
Expected: {"status": "ok", "service": "cofounder-agent"}

# 2. Health with components
GET http://localhost:8000/api/health
Expected: 200 OK with database, LLM status

# 3. Oversight Hub UI - Port 3001
http://localhost:3001
Expected: Login page or dashboard

# 4. Verify services running
npm run dev
# Or check individual tasks:
# - Start Co-founder Agent (port 8000)
# - Start Oversight Hub (port 3001)
```

---

## 🧪 Testing Levels

### Level 1: Backend Chat Endpoint (Direct API Testing)

**Endpoint:** `POST /api/chat`

**Request:**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the capital of France?",
    "model": "ollama-llama2",
    "conversationId": "test-conv-1",
    "temperature": 0.7,
    "max_tokens": 500
  }'
```

**Response Schema:**

```json
{
  "response": "Paris is the capital of France...",
  "model": "ollama-llama2",
  "provider": "ollama",
  "conversationId": "test-conv-1",
  "timestamp": "2026-02-13T12:34:56.789Z",
  "tokens_used": 45
}
```

**Test Cases:**

| Model | Command | Expected Behavior |
|-------|---------|-------------------|
| ollama-llama2 | `ollama-llama2` | Uses local Ollama (requires instance running at :11434) |
| ollama-mistral | `ollama-mistral` | Uses Mistral (requires: `ollama pull mistral`) |
| openai | `openai` | Falls back to OpenAI if OPENAI_API_KEY set |
| claude | `claude` | Falls back to Anthropic if ANTHROPIC_API_KEY set |
| gemini | `gemini` | Falls back to Google if GOOGLE_API_KEY set |

**Validation Checklist:**

- ✅ Response contains "response" field
- ✅ Response contains "model" field matching request
- ✅ Response contains "timestamp" in ISO format
- ✅ conversationId matches request
- ✅ No 500 errors (check backend logs if so)
- ✅ Response time < 30 seconds

---

### Level 2: Natural Language Task Composition

**Endpoint:** `POST /api/tasks/capability/compose-from-natural-language`

**Purpose:** Parse natural language request and compose a capability task

**Request:**

```bash
curl -X POST http://localhost:8000/api/tasks/capability/compose-from-natural-language \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "request": "Write a blog post about artificial intelligence",
    "auto_execute": false,
    "save_task": true
  }'
```

**Expected Response:**

```json
{
  "success": true,
  "task_definition": {
    "id": "task-uuid-123",
    "type": "content_generation",
    "title": "Write a blog post about artificial intelligence",
    "parameters": {
      "topic": "artificial intelligence",
      "content_type": "blog_post",
      "tone": "informative",
      "length": "1000-1500 words"
    },
    "estimated_duration": "120 seconds"
  },
  "explanation": "Composed content generation task"
}
```

**Test Scenarios:**

| Request | Expected Task Type | Model Complexity |
|---------|-------------------|-------------------|
| "Write a blog post about AI" | content_generation | complex |
| "Create a social media post" | content_generation | medium |
| "Summarize this article..." | content_extraction | simple |
| "Analyze market trends" | market_analysis | complex |
| "Check compliance..." | compliance_review | critical |

**Validation Checklist:**

- ✅ Response includes task_definition
- ✅ Task type is recognized (content_generation, etc.)
- ✅ Parameters extracted from natural language
- ✅ Estimated duration reasonable
- ✅ success: true

---

### Level 3: Natural Language + Execution

**Endpoint:** `POST /api/tasks/capability/compose-and-execute`

**Purpose:** Compose AND immediately execute task from natural language

**Request:**

```bash
curl -X POST http://localhost:8000/api/tasks/capability/compose-and-execute \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "request": "Write a tweet about our new product",
    "auto_execute": true,
    "save_task": true
  }'
```

**Expected Response:**

```json
{
  "success": true,
  "execution_id": "exec-uuid-456",
  "task_id": "task-uuid-123",
  "status": "in_progress",
  "estimated_completion": "30 seconds",
  "progress": {
    "stage": "executing",
    "percentage": 0
  }
}
```

**Follow-up: Poll for Results**

```bash
curl http://localhost:8000/api/tasks/exec-uuid-456/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response (while executing):**

```json
{
  "status": "in_progress",
  "percentage": 45,
  "current_stage": "quality_evaluation",
  "message": "Evaluating content quality..."
}
```

**Response (completed):**

```json
{
  "status": "completed",
  "result": {
    "content": "Check out our amazing new product! 🚀 Game-changing innovation...",
    "quality_score": 0.92,
    "criteria_scores": {
      "content_quality": 0.95,
      "engagement": 0.88,
      "brand_voice": 0.91,
      "readability": 0.92
    }
  },
  "published": true,
  "published_platforms": ["twitter", "linkedin"]
}
```

---

### Level 4: WebSocket Real-Time Streaming (Optional)

**Endpoint:** `WS /ws/image-generation/{task_id}`

**Purpose:** Real-time streaming of image generation progress

**JavaScript Client:**

```javascript
const taskId = 'task-abc-123';
const ws = new WebSocket(`ws://localhost:8000/api/ws/image-generation/${taskId}`);

ws.onopen = () => {
  console.log('Connected to task progress stream');
};

ws.onmessage = (event) => {
  const progress = JSON.parse(event.data);
  console.log(`Progress: ${progress.percentage}% - ${progress.message}`);
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = () => {
  console.log('Stream closed');
};
```

**Message Format:**

```json
{
  "type": "progress",
  "task_id": "task-abc-123",
  "status": "generating",
  "current_step": 32,
  "total_steps": 50,
  "percentage": 64.0,
  "current_stage": "base_model",
  "elapsed_time": 46.5,
  "estimated_remaining": 26.3,
  "message": "Generating base image (step 32/50)"
}
```

---

## 🎨 Frontend Testing (Oversight Hub UI)

### Test 1: Natural Language Composer Component

**Location:** Oversight Hub → Natural Language Task Composer Tab

**Steps:**

1. Navigate to Oversight Hub (<http://localhost:3001>)
2. Find "Natural Language Composer" section
3. Enter request: "Write a blog post about machine learning"
4. Click "Compose Task"
5. Verify task appears with parameters
6. Click "Execute Task" (optional)

**Validation:**

- ✅ Task composed within 5 seconds
- ✅ Task parameters show in UI
- ✅ Can preview before execution
- ✅ Execution starts without errors

### Test 2: Orchestrator Page Chat Integration

**Location:** Oversight Hub → Orchestrator Page

**Steps:**

1. Navigate to Orchestrator Page
2. Find user request input field
3. Enter: "Create a newsletter for tech enthusiasts"
4. Click "Submit Request" / "Send"
5. Watch execution progress in real-time
6. Verify orchestration completes

**Validation:**

- ✅ Request submitted successfully
- ✅ Status updates in real-time (every 5 sec polling)
- ✅ Can see orchestration history
- ✅ Final result displayed with quality metrics

### Test 3: Chat Messages Display

**Location:** If chat component exists (check OrchestratorCommandMessage.jsx)

**Steps:**

1. Look for chat/message interface
2. Send test message: "What are the top AI trends?"
3. Verify response appears
4. Check conversation history preserved
5. Try multi-turn conversation

**Validation:**

- ✅ Messages display in order
- ✅ User/assistant messages distinguished
- ✅ Response time reasonable
- ✅ No UI crashes on long responses

---

## 🔍 Debugging & Troubleshooting

### Issue 1: Chat Endpoint Returns 500 Error

**Symptoms:**

```
POST /api/chat → 500 Internal Server Error
```

**Debugging:**

```bash
# 1. Check FastAPI logs
# (Should show in terminal where you ran 'npm run dev:cofounder')
# Look for: [Chat] ERROR: ...

# 2. Verify model availability
curl http://localhost:11434/api/tags
# Should show available Ollama models

# 3. Test model router directly
curl http://localhost:8000/api/models/health
# Should show which providers are available
```

**Solutions:**

- ✅ Ensure Ollama running: `ollama serve` (or Ollama app)
- ✅ Pull required model: `ollama pull llama2`
- ✅ Check API keys in .env.local (OpenAI, Anthropic, Google)
- ✅ Review backend logs for detailed error

### Issue 2: Natural Language Composition Returns Empty Task

**Symptoms:**

```
task_definition: null
Expected parameters not extracted
```

**Debugging:**

```bash
# Check if UnifiedOrchestrator is initialized
curl http://localhost:8000/api/health
# Look for orchestrator status

# Test capability system
curl http://localhost:8000/api/capabilities/registry \
  -H "Authorization: Bearer $TOKEN"
# Should list available capabilities
```

**Solutions:**

- ✅ Ensure database initialized (PostgreSQL running)
- ✅ Check startup logs for errors in capability registration
- ✅ Verify UnifiedQualityService initialized
- ✅ Check task intent router service

### Issue 3: Frontend Not Connecting to Backend

**Symptoms:**

```
CORS error
Network tab shows failed requests to localhost:8000
```

**Debugging:**

```bash
# 1. Check REACT_APP_API_URL in .env
# Should be: http://localhost:8000

# 2. Verify backend CORS is enabled
# Check main.py middleware configuration

# 3. Test direct API call from browser console
fetch('http://localhost:8000/health')
  .then(r => r.json())
  .then(d => console.log(d))
  .catch(e => console.error(e));
```

**Solutions:**

- ✅ Set REACT_APP_API_URL in web/oversight-hub/.env.local
- ✅ Restart frontend (npm run dev:oversight)
- ✅ Check backend CORS middleware allows <http://localhost:3001>
- ✅ Clear browser cache (Ctrl+Shift+Delete)

### Issue 4: WebSocket Connection Fails

**Symptoms:**

```
WebSocket connection failed
ws://localhost:8000/ws/* 404 Not Found
```

**Debugging:**

```bash
# Check WebSocket route is registered
# Search main.py for websocket_router registration

# Test WebSocket manually
websocat ws://localhost:8000/ws/image-generation/test-task-123
```

**Solutions:**

- ✅ Verify WebSocket routes registered in register_all_routes()
- ✅ Use correct endpoint format (no /api prefix for WebSocket)
- ✅ Task ID must be valid UUID format
- ✅ Check for 404s in backend logs

---

## 📊 Example Test Workflow

### Full E2E Test Scenario

```
1. BACKEND HEALTH CHECK
   curl http://localhost:8000/health → ✅ OK

2. MODEL AVAILABILITY
   curl http://localhost:8000/api/models/health
   → ✅ Ollama available at localhost:11434

3. CHAT ENDPOINT TEST
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"Hello","model":"ollama-llama2","conversationId":"test"}'
   → ✅ Response received in <10 seconds

4. NATURAL LANGUAGE COMPOSITION
   curl -X POST http://localhost:8000/api/tasks/capability/compose-from-natural-language \
     -H "Authorization: Bearer $TOKEN" \
     -d '{"request":"Write a blog post about AI"}'
   → ✅ Task composed with parameters

5. TASK EXECUTION
   curl -X POST http://localhost:8000/api/tasks/{task_id}/execute \
     -H "Authorization: Bearer $TOKEN"
   → ✅ Execution started, polling for results

6. FRONTEND UI TEST
   Navigate to http://localhost:3001
   → ✅ Oversight Hub loads
   → ✅ Can navigate to Orchestrator Page
   → ✅ Can enter natural language request
   → ✅ Request processes and shows results

7. QUALITY VERIFICATION
   Check response quality scores
   → ✅ All criteria scores > 0.7
   → ✅ Content makes sense
   → ✅ No truncation/corruption
```

---

## 📈 Performance Metrics to Track

### Response Times

| Operation | Target | Actual | Status |
|-----------|--------|--------|--------|
| Health check | <50ms | ___ ms | ⬜ |
| Chat response (Ollama) | <10s | ___ s | ⬜ |
| Chat response (Claude) | <5s | ___ s | ⬜ |
| Task composition | <3s | ___ s | ⬜ |
| Full workflow (compose + execute) | <120s | ___ s | ⬜ |

### Success Rates

| Operation | Target | Actual | Status |
|-----------|--------|--------|--------|
| Chat endpoint success | 95%+ | ___% | ⬜ |
| Composition success | 90%+ | ___% | ⬜ |
| Execution completion | 85%+ | ___% | ⬜ |
| UI request successful | 99%+ | ___% | ⬜ |

### Quality Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Average quality score | 0.80+ | _____ | ⬜ |
| Content relevance | 0.85+ | _____ | ⬜ |
| Brand voice consistency | 0.80+ | _____ | ⬜ |
| Engagement potential | 0.75+ | _____ | ⬜ |

---

## 🛠️ Tools & Utilities

### Recommended Testing Tools

1. **Curl** - Backend API testing

   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"test","model":"ollama-llama2","conversationId":"1"}'
   ```

2. **Postman** - API testing with collections
   - Import: Backend OpenAPI at <http://localhost:8000/api/openapi.json>
   - Create test collection for chat endpoints
   - Save environment variables (base URL, token, etc.)

3. **WebSocket Client** - Test real-time features

   ```bash
   # Install websocat: cargo install websocat
   websocat ws://localhost:8000/ws/image-generation/task-123
   ```

4. **Browser DevTools**
   - Network tab: Monitor API requests
   - Console: Test fetch/WebSocket from browser
   - Performance: Measure response times

5. **Backend Logs**
   - Location: Terminal running `npm run dev:cofounder`
   - Filter: Search for [Chat], [Orchestrator], [Task]
   - Verbosity: Set `LOG_LEVEL=debug` in .env.local

### Quick Test Scripts

**kill-terminals.sh** - Reset services

```bash
#!/bin/bash
# Kill running services and restart
npm run stop:all 2>/dev/null || true
sleep 2
npm run dev &
sleep 10
echo "✅ Services restarted"
```

**test-chat-api.sh** - Full chat test

```bash
#!/bin/bash
echo "Testing Chat API..."
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is 2+2?",
    "model": "ollama-llama2",
    "conversationId": "test-1"
  }' | jq '.'
```

**test-composition.sh** - Test NLP composition

```bash
#!/bin/bash
TOKEN=${1:-"your-token-here"}
curl -X POST http://localhost:8000/api/tasks/capability/compose-from-natural-language \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": "Write a blog post about AI safety",
    "auto_execute": false,
    "save_task": true
  }' | jq '.'
```

---

## ✨ Best Practices

### When Testing Chat/NLP Features

1. **Always verify model availability first**

   ```bash
   # Check Ollama
   ollama list
   # Check API keys
   echo $OPENAI_API_KEY
   ```

2. **Use meaningful test prompts**
   - ✅ "Write a blog post about AI"
   - ❌ "test"
   - ✅ "Compose a social media post for product launch"
   - ❌ "hello"

3. **Check both success and edge cases**
   - Normal request → 200 OK
   - Invalid model → Graceful fallback
   - No API keys → Echo response
   - Timeout → 504 Gateway Timeout after 30s

4. **Monitor backend logs during testing**
   - Watch for [Chat] log entries
   - Note any warnings or errors
   - Track token usage if available

5. **Profile performance metrics**
   - Measure response times
   - Compare providers (Ollama vs Claude vs GPT)
   - Document baseline metrics
   - Alert on regressions (>50% slower)

6. **Test conversation history**
   - Send multi-message conversations
   - Verify context preserved
   - Check for memory leaks (growing size)

7. **Test error handling**
   - Invalid JSON → 422 Unprocessable Entity
   - Missing fields → Validation error
   - Model not found → Fallback message
   - Database error → 500 with error ID

---

## 📝 Reporting Issues

When reporting test failures, include:

1. **Request Details**
   - Exact endpoint URL
   - Request body (sanitize tokens)
   - Headers sent

2. **Response Details**
   - Status code
   - Response body
   - Response time

3. **System State**
   - Services running (backend, frontend, Ollama)
   - Environment variables set
   - .env.local contents (sanitized)

4. **Logs**
   - Backend logs (from terminal)
   - Browser console errors
   - Network tab (if UI issue)

5. **Reproduction Steps**
   - Exact steps to reproduce
   - Expected vs actual behavior
   - Frequency (always/intermittent)

**Example Issue Report:**

```
Title: Chat endpoint returns 500 on Ollama requests

Steps:
1. POST to http://localhost:8000/api/chat
2. Use model "ollama-llama2"
3. Message: "Hello world"

Expected: 200 OK with response
Actual: 500 Internal Server Error

Backend Log:
[Chat] ERROR: Ollama connection failed: Connection refused
[Chat] Fallback to Claude failed: No ANTHROPIC_API_KEY

Environment:
- Ollama NOT running
- ANTHROPIC_API_KEY not set
- Model Router should use fallback...
```

---

## 🎓 Learning Resources

- [Chat Routes Code](../src/cofounder_agent/routes/chat_routes.py)
- [Natural Language Composer Service](../web/oversight-hub/src/services/naturalLanguageComposerService.js)
- [Orchestrator Page Component](../web/oversight-hub/src/pages/OrchestratorPage.jsx)
- [Model Router (Cost Optimization)](../src/cofounder_agent/services/model_router.py)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [WebSocket Testing](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)

---

## 📋 Checklist for Daily Testing

- [ ] Backend health check passes (GET /health)
- [ ] Chat endpoint responds (POST /api/chat)
- [ ] Ollama available or API keys configured
- [ ] Frontend loads (<http://localhost:3001>)
- [ ] Natural language composer works
- [ ] Task execution completes
- [ ] No duplicate requests in logs
- [ ] Quality scores reasonable (0.7+)
- [ ] No critical errors in console
- [ ] All 3 services responding (backend, hub, public site)

---

## 📞 Support & Escalation

- **Backend Issues** → Check `src/cofounder_agent/services/` logs
- **Frontend Issues** → Check browser console, Network tab
- **Chat Not Working** → Verify `OPENAI_API_KEY` or Ollama running
- **Composition Failing** → Check database and UnifiedOrchestrator initialization
- **WebSocket Issues** → Verify task ID format and endpoint URL
