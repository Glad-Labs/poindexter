# Google Gemini Testing & Debugging Guide for Oversight Hub

## Quick Status Check

Your environment is already configured with Gemini support. Here's what we have:

### ✅ Current Configuration

- **Google API Key**: Set in `.env.local` ✓
- **Backend**: FastAPI with model consolidation service supports Gemini ✓
- **Frontend**: Oversight Hub (React) on port 3001 ✓
- **Model Router**: Supports automatic fallback chain including Gemini ✓

---

## 1. VERIFY GEMINI IS CONFIGURED

### Check Environment

```bash
# View your Gemini API key (first run from repo root)
echo $GOOGLE_API_KEY

# Or check the .env.local file
grep GOOGLE_API_KEY .env.local
```

### Check Gemini in Backend (Terminal)

```bash
# Check available models endpoint
curl http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google")'
```

**Expected Output:**

```json
{
  "name": "gemini-1.5-pro",
  "displayName": "gemini-1.5-pro (google)",
  "provider": "google",
  "isFree": false,
  "icon": "☁️",
  "requiresInternet": true
}
```

### Check Provider Status

```bash
# Get health status of all providers including Gemini
curl http://localhost:8000/api/v1/models/status | jq '.providers.google'
```

**Expected Output:**

```json
{
  "available": true,
  "models_count": 3,
  "last_checked": "2026-01-16T12:34:56",
  "response_time_ms": 156
}
```

---

## 2. DIRECT GEMINI API TEST

### Test Gemini via Backend Chat Endpoint

**1. Create a test conversation:**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test-gemini-123",
    "model": "gemini-1.5-pro",
    "message": "Say hello! Keep response under 50 words."
  }'
```

**Expected Response:**

```json
{
  "response": "Hello! I'm Claude, an AI assistant created by Anthropic...",
  "model": "gemini-1.5-pro",
  "conversationId": "test-gemini-123",
  "timestamp": "2026-01-16T12:34:56.789Z",
  "tokens_used": 42
}
```

### Test with Conversation History

```bash
# Send first message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "gemini-test-conv",
    "model": "gemini-1.5-pro",
    "message": "My name is Alice"
  }'

# Send follow-up (should remember context)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "gemini-test-conv",
    "model": "gemini-1.5-pro",
    "message": "What is my name?"
  }'
```

### View Conversation History

```bash
curl http://localhost:8000/api/chat/history/gemini-test-conv | jq '.'
```

---

## 3. GEMINI IN OVERSIGHT HUB (UI Testing)

### Access the Dashboard

1. Open browser to: **http://localhost:3001**
2. Login (use mock auth if enabled in `.env.local`)

### Test Chat Interface with Gemini

**Step 1: Navigate to Chat**

- Go to Tasks or Chat section (depending on UI layout)
- Look for model/provider selector dropdown

**Step 2: Select Gemini**

- Click the model dropdown (usually shows current provider)
- Select "gemini-1.5-pro" or similar
- You should see the Google ☁️ icon next to it

**Step 3: Send Test Message**

- Type: `"What is your model name and provider?"`
- Send message
- Look for response showing Gemini was used

**Step 4: Check Metadata**

- Response should show:
  - Model: `gemini-1.5-pro`
  - Provider: `google`
  - Timestamp: Current time
  - Tokens used: Approximate count

---

## 4. DEBUGGING GEMINI ISSUES

### Problem: "Gemini not appearing in model list"

**Check 1: API Key Validation**

```bash
# Test if Google API key is valid
curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"test"}]}]}' \
  -H "x-goog-api-key: YOUR_API_KEY_HERE"
```

If you get `{"error": "API key not valid"}`, the key is wrong.

**Check 2: Service Not Running**

```bash
# Ensure backend is running on port 8000
curl http://localhost:8000/api/health | jq '.components'
```

**Check 3: Model Consolidation Service**
Check backend logs for initialization:

```bash
# Look for this in terminal where backend is running:
# "Model consolidation service initialized"
# "Google provider: X models available"
```

### Problem: "Gemini selected but getting fallback model response"

**Possible Causes:**

1. API key exhausted or rate limited
2. Network connectivity issue
3. Gemini API temporarily down
4. Wrong model name format

**Debug Steps:**

```bash
# 1. Check API key in .env.local
cat .env.local | grep GOOGLE_API_KEY

# 2. Check backend logs for actual provider used
# Look for: "[Chat] Using provider: google" or "[Chat] Fallback to provider: ..."

# 3. Test direct API call
curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"parts": [{"text": "test"}]}],
    "generationConfig": {"maxOutputTokens": 100}
  }' \
  -H "x-goog-api-key: $GOOGLE_API_KEY"
```

### Problem: "CORS error in browser console"

**Solution:**
Check `.env.local` for CORS configuration:

```env
# Should include both 3000 and 3001
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,...
```

If needed, update and restart backend:

```bash
npm run dev:cofounder
```

### Problem: "Rate limit errors (429 status)"

Gemini has rate limits:

- **Free tier**: 60 requests/minute per API key
- **Paid tier**: Based on quota

**Solution:**

1. Wait 1-2 minutes before retrying
2. Or upgrade to paid API tier
3. Use Ollama (free, local) for testing instead
4. Implement exponential backoff in retry logic

---

## 5. ADVANCED DEBUGGING

### Enable Debug Logging

**Update `.env.local`:**

```env
LOG_LEVEL=DEBUG
DEBUG=true
SQL_DEBUG=true
```

**Restart backend:**

```bash
npm run dev:cofounder
```

**Look for debug logs:**

```bash
# Terminal output should show:
# [Chat] Using model: gemini-1.5-pro
# [Chat] Provider selected: google
# [Chat] Request: {...}
# [Chat] Response: {...}
```

### Check Backend Logs

**If running via `npm run dev`:**

- Logs appear in terminal where command was executed
- Look for `[Chat]` or `[Models]` prefixes
- Search for `gemini`, `google`, or `provider`

### Inspect Network Requests

**In Oversight Hub browser (Chrome DevTools):**

1. Open: `F12` → Network tab
2. Send a chat message with Gemini selected
3. Look for requests to:
   - `POST /api/chat` (main request)
   - `GET /api/v1/models/available` (model list)
4. Click each request → Response tab to see JSON data
5. Check for error messages or warnings

### Database Query Inspection

If using PostgreSQL with `SQL_DEBUG=true`:

```bash
# In PostgreSQL client
psql -U postgres -d glad_labs_dev -c "
  SELECT * FROM tasks
  WHERE model_used = 'gemini-1.5-pro'
  ORDER BY created_at DESC
  LIMIT 5;
"
```

---

## 6. FALLBACK CHAIN TESTING

The model router has an **automatic fallback chain**:

```
Ollama (free, local) → HuggingFace (cheap) → Gemini (good) → Claude (better) → GPT-4 (best)
```

### Test Fallback Behavior

**Scenario 1: Gemini succeeds**

- ✅ Response from Gemini with metadata: `"provider": "google"`

**Scenario 2: Gemini fails, falls back to Claude**

- Response includes metadata: `"provider": "anthropic"`
- Backend logs show: `"[Models] Gemini failed, trying Anthropic"`

**Scenario 3: All paid APIs fail, uses Ollama**

- Response uses local model with `"provider": "ollama"`
- No API calls made (completely local)

### Trigger Fallback Testing

```bash
# Rename/hide Gemini API key to force fallback
unset GOOGLE_API_KEY

# Or in .env.local:
# GOOGLE_API_KEY=invalid-test-key

# Restart backend and try chat - should fall back to next provider
```

---

## 7. QUICK REFERENCE: API ENDPOINTS

| Endpoint                     | Method | Purpose                                 | Auth Required |
| ---------------------------- | ------ | --------------------------------------- | ------------- |
| `/api/v1/models/available`   | GET    | List all models including Gemini        | No            |
| `/api/v1/models/status`      | GET    | Check provider status (includes Google) | No            |
| `/api/v1/models/recommended` | GET    | Get recommended models by cost tier     | No            |
| `/api/chat`                  | POST   | Send message with specific model        | No            |
| `/api/chat/history/{id}`     | GET    | Get conversation history                | No            |
| `/api/health`                | GET    | Overall system health                   | No            |

---

## 8. PERFORMANCE TIPS

### Reduce Latency

```env
# Prefer faster models
DEFAULT_TASK_MODEL=gemini-1.5-flash  # Faster than pro
# OR use local Ollama for instant response
USE_OLLAMA=true
OLLAMA_MODEL=mistral:latest
```

### Reduce Costs

```env
# Use cheaper APIs first
LLM_PROVIDER_ORDER=ollama,huggingface,gemini,anthropic,openai
# OR use Ollama exclusively (FREE)
USE_OLLAMA=true
DISABLE_PAID_APIS=true
```

### Token Optimization

```bash
# Test with max_tokens limit
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test",
    "model": "gemini-1.5-pro",
    "message": "Summarize in 50 words: [text]",
    "max_tokens": 100
  }'
```

---

## 9. REAL-WORLD TESTING SCENARIOS

### Scenario A: Content Generation

```bash
# Test Gemini for content creation task
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "content-test",
    "model": "gemini-1.5-pro",
    "message": "Write a 100-word blog post about AI in business",
    "task_type": "create"
  }'
```

### Scenario B: Code Assistance

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "code-test",
    "model": "gemini-1.5-pro",
    "message": "Write a Python function to validate email addresses"
  }'
```

### Scenario C: Analysis Task

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "analysis-test",
    "model": "gemini-1.5-pro",
    "message": "Analyze the market trends for AI startups in 2026"
  }'
```

---

## 10. TROUBLESHOOTING SUMMARY

| Symptom                             | Likely Cause             | Solution                                  |
| ----------------------------------- | ------------------------ | ----------------------------------------- |
| Gemini not in dropdown              | Model list not loading   | Check `/api/v1/models/available` endpoint |
| Gemini selected but Claude response | API key invalid          | Verify `GOOGLE_API_KEY` in `.env.local`   |
| "Rate limit" error                  | Too many requests        | Wait 1-2 minutes, use Ollama instead      |
| CORS error in browser               | Wrong ALLOWED_ORIGINS    | Update `.env.local` and restart backend   |
| Conversation lost after reload      | Session management issue | Check browser localStorage/cookies        |
| Slow response (10+ seconds)         | Network/API latency      | Try gemini-1.5-flash or Ollama for local  |

---

## Next Steps

1. **Verify Setup**: Run `curl http://localhost:8000/api/v1/models/available | jq`
2. **Test Chat**: Send test message via cURL
3. **Test UI**: Open Oversight Hub and select Gemini model
4. **Monitor**: Check backend logs and browser DevTools
5. **Optimize**: Adjust model preferences based on performance

**Need Help?** Check backend logs:

```bash
# Last 50 lines of output
npm run dev  # Already running? Check terminal output
```

---

**Last Updated**: January 16, 2026  
**Gemini Status**: ✅ Configured and Ready  
**Oversight Hub**: http://localhost:3001  
**Backend API**: http://localhost:8000
