# Quick Gemini Testing - 5 Minute Setup

## Step 1: Verify Services Running (30 seconds)

```bash
# Make sure these are running in separate terminals:
# Terminal 1: npm run dev:cofounder (or npm run dev for all 3)
# Terminal 2: npm run dev (from web/public-site if separate)
# Terminal 3: npm start (from web/oversight-hub if separate)

# Quick check all services:
curl -s http://localhost:8000/api/health | jq .status
# Expected: "healthy" or "starting"
```

## Step 2: Test Gemini Directly (2 minutes)

### Option A: Quick Terminal Test

```bash
# Test 1: Check if Gemini is available
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google")'

# Test 2: Send a message to Gemini
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "quick-test",
    "model": "gemini-1.5-pro",
    "message": "Say hello in one sentence"
  }' | jq '.'

# Expected output includes: "response": "...", "provider": "google"
```

### Option B: PowerShell Test (Windows)

```powershell
# Run our test script
.\scripts\test-gemini.ps1
```

### Option C: Bash Test (Mac/Linux)

```bash
# Run our test script
bash scripts/test-gemini.sh
```

## Step 3: Test in UI (2-3 minutes)

1. **Open Oversight Hub:**
   - Browser: http://localhost:3001
   - Login (use mock auth)

2. **Find Chat/Tasks Section:**
   - Look for a chat interface or task creation form

3. **Select Gemini:**
   - Find the model/provider dropdown
   - Select "gemini-1.5-pro" (should have ☁️ icon)

4. **Send Test Message:**
   - Type: "What is your model name?"
   - Send

5. **Check Response:**
   - Look for metadata showing:
     - Provider: "google"
     - Model: "gemini-1.5-pro"
     - Timestamp

## Step 4: Check Logs (1 minute)

Look at the terminal where backend is running:

```
[Chat] Received request with model: gemini-1.5-pro
[Chat] Using provider: google
[Chat] Response from provider: success (156ms)
```

---

## Common Quick Issues & Fixes

### Issue: "Gemini not in dropdown"

**Fix:** Backend not running

```bash
npm run dev:cofounder
# Wait 10 seconds for startup
curl http://localhost:8000/api/health
```

### Issue: "Getting Claude response instead of Gemini"

**Fix:** API key not valid

```bash
# Check .env.local
cat .env.local | grep GOOGLE_API_KEY

# Test directly at Google (replace YOUR_KEY_HERE)
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key=YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"test"}]}]}'
```

### Issue: "Rate limit error after a few messages"

**Fix:** Using free tier (60 requests/minute)

- Wait 1-2 minutes
- OR use Ollama for testing (unlimited, free, local)

```bash
# Set in .env.local
USE_OLLAMA=true
OLLAMA_MODEL=mistral:latest
```

### Issue: "CORS error in browser console"

**Fix:** Restart backend with correct origins

1. Edit `.env.local`, ensure:
   ```env
   ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,...
   ```
2. Restart:
   ```bash
   npm run dev:cofounder
   ```

---

## Success Checklist

- [ ] All three services running (backend, public-site, oversight-hub)
- [ ] `/api/health` returns "healthy" or "starting"
- [ ] `/api/v1/models/available` shows gemini-1.5-pro
- [ ] Gemini chat test returns "provider": "google"
- [ ] Oversight Hub loads without CORS errors
- [ ] Gemini appears in model dropdown
- [ ] Can send message and get Gemini response

---

## Next: Deep Debugging

If something isn't working, check the full guide:
[GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)

**Key sections:**

- Section 4: Debugging Gemini Issues
- Section 5: Advanced Debugging
- Terminal command examples
- Network request inspection

---

## Performance Baselines

**Response Times (Gemini 1.5 Pro):**

- Simple greeting: 1-2 seconds
- 3-sentence summary: 2-4 seconds
- Code generation: 3-5 seconds
- Slow network: 10+ seconds (check internet)

**Cost (for reference):**

- Free tier: 60 requests/minute
- Paid: $0.075 per 1M input tokens / $0.30 per 1M output tokens

---

**Status:** ✅ Gemini configured and ready to test
**Time to test:** ~5 minutes
**Expected success rate:** 95%+ (if services running)
