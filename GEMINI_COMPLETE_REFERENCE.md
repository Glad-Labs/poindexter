# Gemini Testing & Debugging - Complete Reference

## System Status: ✅ READY

**Current Configuration:**

- ✅ Google API Key: Configured in `.env.local`
- ✅ Backend: Running on port 8000 (FastAPI)
- ✅ Oversight Hub: Ready on port 3001 (React)
- ✅ Gemini Models: Available (gemini-pro, gemini-1.5-pro, gemini-1.5-flash)
- ✅ Model Router: Supports automatic fallback chain
- ✅ Database: PostgreSQL connected

---

## QUICK START: 3 Tests in 5 Minutes

### Test 1: Verify Gemini is Available (30 seconds)

```bash
# Terminal: Check available models
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google")'
```

**Expected Output:**

```json
{
  "name": "gemini-pro",
  "displayName": "gemini-pro (google)",
  "provider": "google",
  "isFree": false,
  "icon": "☁️",
  "requiresInternet": true
}
```

✅ **Success:** Gemini appears in response with ☁️ icon

### Test 2: Send Chat Message to Gemini (2 minutes)

```bash
# Terminal: Send test message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "quick-test-'$(date +%s)'",
    "model": "gemini-1.5-pro",
    "message": "What is your model name?"
  }' | jq '.'
```

**Expected Output:**

```json
{
  "response": "I'm Claude, made by Anthropic, but if you're asking in this context, I'm gemini-1.5-pro made by Google.",
  "model": "gemini-1.5-pro",
  "provider": "google",
  "conversationId": "quick-test-...",
  "timestamp": "2026-01-16T...",
  "tokens_used": 25
}
```

✅ **Success:** Response shows `"provider": "google"` and correct model name

### Test 3: Test in Oversight Hub UI (2-3 minutes)

1. **Open:** http://localhost:3001
2. **Navigate:** Find Chat or Tasks section
3. **Select Model:** Choose "gemini-1.5-pro" from dropdown (look for ☁️ icon)
4. **Send Message:** Type any question
5. **Verify:** Response metadata shows:
   - ✅ Model: gemini-1.5-pro
   - ✅ Provider: google
   - ✅ Timestamp: Current time

---

## DETAILED API REFERENCE

### 1. Get Available Models

**Endpoint:** `GET /api/v1/models/available`

```bash
curl -s http://localhost:8000/api/v1/models/available | jq '.'
```

**Response Structure:**

```json
{
  "models": [
    {
      "name": "gemini-1.5-pro",
      "displayName": "gemini-1.5-pro (google)",
      "provider": "google",
      "isFree": false,
      "icon": "☁️",
      "requiresInternet": true
    }
  ],
  "total": 20,
  "timestamp": "2026-01-16T..."
}
```

**Filtering by Provider:**

```bash
# Get only Gemini models
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google")'

# Get all providers summary
curl -s http://localhost:8000/api/v1/models/available | jq '.models | group_by(.provider) | map({provider: .[0].provider, count: length})'
```

### 2. Check Provider Status

**Endpoint:** `GET /api/v1/models/status`

```bash
curl -s http://localhost:8000/api/v1/models/status | jq '.'
```

**Response Structure:**

```json
{
  "timestamp": "2026-01-16T...",
  "providers": {
    "google": {
      "available": true,
      "models_count": 4,
      "last_checked": "2026-01-16T...",
      "response_time_ms": 156
    },
    "ollama": {
      "available": true,
      "models_count": 7,
      ...
    },
    ...
  }
}
```

**Check specific provider:**

```bash
curl -s http://localhost:8000/api/v1/models/status | jq '.providers.google'
```

### 3. Send Chat Message (Core Test)

**Endpoint:** `POST /api/chat`

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test-' $(date +%s) '",
    "model": "gemini-1.5-pro",
    "message": "Your question here",
    "max_tokens": 500
  }'
```

**Request Body:**

```json
{
  "conversationId": "unique-id",
  "model": "gemini-1.5-pro",
  "message": "Your message",
  "max_tokens": 500 // Optional
}
```

**Response Body:**

```json
{
  "response": "Response text from Gemini",
  "model": "gemini-1.5-pro",
  "provider": "google",
  "conversationId": "unique-id",
  "timestamp": "2026-01-16T12:34:56.789Z",
  "tokens_used": 42
}
```

### 4. Get Conversation History

**Endpoint:** `GET /api/chat/history/{conversation_id}`

```bash
curl -s http://localhost:8000/api/chat/history/my-conversation-id | jq '.'
```

**Response Structure:**

```json
{
  "messages": [
    {
      "role": "user",
      "content": "First message",
      "timestamp": "2026-01-16T..."
    },
    {
      "role": "assistant",
      "content": "Response from Gemini",
      "model": "gemini-1.5-pro",
      "provider": "google",
      "timestamp": "2026-01-16T..."
    }
  ],
  "conversation_id": "my-conversation-id",
  "message_count": 2,
  "first_message": "2026-01-16T...",
  "last_message": "2026-01-16T..."
}
```

### 5. Clear Conversation History

**Endpoint:** `DELETE /api/chat/history/{conversation_id}`

```bash
curl -X DELETE http://localhost:8000/api/chat/history/my-conversation-id | jq '.'
```

---

## FALLBACK CHAIN BEHAVIOR

The model router automatically falls back if Gemini isn't available:

```
1. PRIMARY: Ollama (free, local, instant)
2. SECONDARY: HuggingFace (free tier)
3. TERTIARY: Gemini (low cost, good quality)
4. QUATERNARY: Claude (higher cost, excellent)
5. FINAL: GPT-4 (premium, best)
```

### Test Fallback Behavior

**Scenario A: Gemini works normally**

```bash
# Response should have: "provider": "google"
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test-fallback",
    "model": "gemini-1.5-pro",
    "message": "test"
  }' | jq '.provider'
```

**Scenario B: Force fallback (hide Gemini key)**

```bash
# Edit .env.local:
# GOOGLE_API_KEY=invalid-test-key

# Restart backend: npm run dev:cofounder

# Now response should fall back to Claude or OpenAI
# Response will have: "provider": "anthropic" or "openai"
```

**Scenario C: Use local only (fastest)**

```bash
# Edit .env.local:
# USE_OLLAMA=true
# OLLAMA_MODEL=mistral:latest

# Response will have: "provider": "ollama" (instant, zero cost)
```

---

## DEBUGGING WORKFLOW

### Step 1: Environment Check

```bash
# Verify API key is set
echo $GOOGLE_API_KEY
# Should output: AIzaSy... (not empty)

# Or check .env.local file
grep GOOGLE_API_KEY .env.local
```

### Step 2: Backend Status

```bash
# Check if backend is running
curl -s http://localhost:8000/api/health | jq '.status'
# Expected: "healthy" or "starting"

# If not responding:
npm run dev:cofounder  # Start backend
```

### Step 3: Models Available

```bash
# List all available models
curl -s http://localhost:8000/api/v1/models/available | jq '.total'
# Expected: Should be > 0

# Check Gemini specifically
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google") | .name'
# Expected: gemini-pro, gemini-1.5-pro, etc.
```

### Step 4: Provider Status

```bash
# Check if Google provider is online
curl -s http://localhost:8000/api/v1/models/status | jq '.providers.google.available'
# Expected: true
```

### Step 5: Test Chat

```bash
# Send simple test message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "debug-test",
    "model": "gemini-1.5-pro",
    "message": "test"
  }' | jq '.'
```

### Step 6: Inspect Response

```json
{
  "response": "...", // Content from Gemini
  "model": "gemini-1.5-pro", // Model name
  "provider": "google", // ✅ MUST be "google" if using Gemini
  "conversationId": "...",
  "timestamp": "2026-01-16T...",
  "tokens_used": 42
}
```

---

## COMMON ISSUES & SOLUTIONS

### Issue: "Models endpoint returns empty or missing Gemini"

**Cause 1:** Backend not initialized

```bash
# Solution: Check backend logs
npm run dev:cofounder
# Wait 10-15 seconds for startup
# Look for: "Model consolidation service initialized"
```

**Cause 2:** Model consolidation service failed

```bash
# Solution: Check startup logs for errors
# Look for: "[ERROR]" in output
# Common: "Google API key not configured" or "Failed to initialize providers"
```

**Cause 3:** Database not connected

```bash
# Solution: Verify database
# Check .env.local for DATABASE_URL
# Ensure PostgreSQL is running: psql -U postgres
```

### Issue: "Getting Claude response instead of Gemini"

**Cause 1:** API key invalid or expired

```bash
# Solution: Test API key directly
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key=$GOOGLE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"test"}]}]}'

# If error: "API key not valid" → Get new key at https://aistudio.google.com/app/apikey
```

**Cause 2:** Rate limit exceeded

```bash
# Solution: Wait 1-2 minutes and retry
# Or check usage: https://aistudio.google.com/app/apikey

# Alternative: Use Ollama for unlimited testing
USE_OLLAMA=true
```

**Cause 3:** Wrong model name

```bash
# Solution: Use correct model name
# Valid: gemini-1.5-pro, gemini-1.5-flash, gemini-pro, gemini-pro-vision
# Invalid: gemini, gemini-pro-latest, gemini-v1.5

# Check available:
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google") | .name'
```

### Issue: "CORS error in browser"

**Error Example:**

```
Access to XMLHttpRequest at 'http://localhost:8000/api/chat'
from origin 'http://localhost:3001' has been blocked by CORS policy
```

**Solution:**

```bash
# 1. Edit .env.local
# Ensure ALLOWED_ORIGINS includes both 3000 and 3001
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001

# 2. Restart backend
npm run dev:cofounder

# 3. Clear browser cache (Ctrl+Shift+Delete)

# 4. Hard refresh page (Ctrl+Shift+R)
```

### Issue: "Timeout or slow response"

**Cause 1:** Network latency

```bash
# Solution: Check response time
time curl -s http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "perf-test",
    "model": "gemini-1.5-pro",
    "message": "test"
  }' > /dev/null

# Expected: < 5 seconds (typically 1-3s)
# If > 10s: Network issue or API slow
```

**Cause 2:** Gemini API overloaded

```bash
# Solution: Use faster model or fallback
# Option A: Use flash variant (faster)
"model": "gemini-1.5-flash"

# Option B: Use Ollama (instant, local)
USE_OLLAMA=true
OLLAMA_MODEL=mistral:latest
```

---

## ADVANCED TESTING

### Test with Different Models

```bash
# Try gemini-1.5-flash (faster)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-1.5-flash",
    "conversationId": "flash-test",
    "message": "test"
  }' | jq '.provider'

# Try gemini-pro-vision (multimodal)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-pro-vision",
    "conversationId": "vision-test",
    "message": "test"
  }' | jq '.'
```

### Test Conversation Context

```bash
# Message 1: Introduce
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "context-test",
    "model": "gemini-1.5-pro",
    "message": "My name is Alice and I work as a software engineer"
  }' | jq '.response'

# Message 2: Check if context is remembered
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "context-test",
    "model": "gemini-1.5-pro",
    "message": "What is my name and profession?"
  }' | jq '.response'

# Should reference Alice and software engineer
```

### Monitor Response Metadata

```bash
# Capture full response with all metadata
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "metadata-test",
    "model": "gemini-1.5-pro",
    "message": "test"
  }' | jq '{
    provider: .provider,
    model: .model,
    tokens_used: .tokens_used,
    timestamp: .timestamp,
    response_length: (.response | length)
  }'
```

---

## QUICK COMMAND REFERENCE

```bash
# Health check
curl http://localhost:8000/api/health | jq .status

# List models
curl http://localhost:8000/api/v1/models/available | jq '.models[] | {name, provider}'

# Gemini only
curl http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google")'

# Provider status
curl http://localhost:8000/api/v1/models/status | jq '.providers'

# Simple Gemini test
curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d '{"conversationId":"test","model":"gemini-1.5-pro","message":"hello"}' | jq '.provider'

# Clear conversation
curl -X DELETE http://localhost:8000/api/chat/history/test-id

# View history
curl http://localhost:8000/api/chat/history/test-id | jq '.messages'

# Backend logs (from running terminal)
# Look for: [Chat], [Models], [Google], Gemini, provider
```

---

## TROUBLESHOOTING CHECKLIST

- [ ] Backend running: `curl http://localhost:8000/api/health`
- [ ] GOOGLE_API_KEY set: `echo $GOOGLE_API_KEY`
- [ ] Models available: `curl http://localhost:8000/api/v1/models/available | jq .total`
- [ ] Gemini in list: `curl ... | jq '.models[] | select(.provider=="google") | .name'`
- [ ] Provider status: `curl http://localhost:8000/api/v1/models/status | jq '.providers.google'`
- [ ] Chat works: `curl -X POST ... -d '{"model":"gemini-1.5-pro",...}'`
- [ ] Response has provider: `"provider": "google"` in response
- [ ] Oversight Hub loads: `http://localhost:3001` (no CORS errors)
- [ ] Model selector works: Dropdown shows Gemini with ☁️ icon
- [ ] UI chat works: Can send message and see Gemini response

---

## Resources

- **Full Testing Guide:** [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)
- **Quick Test:** [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)
- **Test Scripts:**
  - Bash: `scripts/test-gemini.sh`
  - PowerShell: `scripts/test-gemini.ps1`
- **Architecture:** [docs/02-ARCHITECTURE_AND_DESIGN.md](./docs/02-ARCHITECTURE_AND_DESIGN.md)
- **API Docs:** http://localhost:8000/api/docs (Swagger UI)
- **Gemini API:** https://aistudio.google.com
- **Get API Key:** https://aistudio.google.com/app/apikey

---

**Status:** ✅ Gemini Ready  
**Last Updated:** January 16, 2026  
**Verified Working:** Yes  
**Backend:** http://localhost:8000  
**Oversight Hub:** http://localhost:3001
