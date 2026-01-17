# Gemini Testing & Debugging - Quick Summary

## What's Been Set Up For You

I've created **comprehensive testing guides and tools** for Google Gemini in your Oversight Hub. Everything is ready to use.

### ✅ Resources Created

1. **GEMINI_COMPLETE_REFERENCE.md** - Full API documentation and reference guide
   - All endpoints documented with examples
   - Debugging workflow step-by-step
   - Common issues and solutions
   - Advanced testing scenarios

2. **GEMINI_TEST_DEBUG_GUIDE.md** - Detailed testing and debugging guide
   - 10 complete test scenarios
   - Performance benchmarks
   - Real-world testing scenarios
   - Troubleshooting summary

3. **GEMINI_QUICK_TEST.md** - 5-minute quick start
   - Simple setup and verification
   - Quick terminal tests
   - UI testing steps
   - Common quick fixes

4. **scripts/test-gemini.sh** - Bash test script
   - 10 automated tests
   - Works on Mac/Linux
   - Color-coded output
   - Performance metrics

5. **scripts/test-gemini.ps1** - PowerShell test script
   - 10 automated tests
   - Works on Windows
   - Color-coded output
   - Same functionality as bash version

---

## Current Status: ✅ READY

- ✅ Gemini API Key: **Configured** in `.env.local`
- ✅ Backend: **Running** on port 8000
- ✅ Oversight Hub: **Ready** on port 3001
- ✅ Gemini Models: **Available** (gemini-pro, gemini-1.5-pro, gemini-1.5-flash, gemini-pro-vision)
- ✅ Automatic Fallback: **Enabled** (Ollama → HuggingFace → Gemini → Claude → GPT-4)

---

## How to Test (Choose One)

### Option 1: Quick Terminal Test (30 seconds)

```bash
# Check if Gemini is available
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google") | .name'

# Send a test message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversationId":"test","model":"gemini-1.5-pro","message":"What is your model name?"}' | jq '.provider'

# Expected output: "google"
```

### Option 2: Run Automated Test Script (2 minutes)

```bash
# Windows (PowerShell)
.\scripts\test-gemini.ps1

# Mac/Linux (Bash)
bash scripts/test-gemini.sh
```

### Option 3: Test in Oversight Hub UI (2-3 minutes)

1. Open http://localhost:3001
2. Navigate to chat/tasks section
3. Select "gemini-1.5-pro" from model dropdown (look for ☁️ icon)
4. Send a test message
5. Verify response shows provider: "google"

---

## Documentation Guide

| Document                         | Best For                   | Read Time |
| -------------------------------- | -------------------------- | --------- |
| **GEMINI_QUICK_TEST.md**         | Getting started fast       | 5 min     |
| **GEMINI_COMPLETE_REFERENCE.md** | API reference and examples | 10 min    |
| **GEMINI_TEST_DEBUG_GUIDE.md**   | Detailed debugging help    | 15 min    |

---

## Common First Tests

### Test 1: Verify Setup

```bash
# Check environment
grep GOOGLE_API_KEY .env.local

# Check backend running
curl http://localhost:8000/api/health | jq .status

# Check Gemini available
curl http://localhost:8000/api/v1/models/available | jq '.models | length'
```

**Expected:**

- ✅ GOOGLE_API_KEY shows a key
- ✅ Status: "healthy" or "starting"
- ✅ Models: > 0

### Test 2: Simple Chat

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test-'$(date +%s)'",
    "model": "gemini-1.5-pro",
    "message": "Say hello"
  }' | jq '.'
```

**Check in response:**

- ✅ "provider": "google"
- ✅ "model": "gemini-1.5-pro"
- ✅ "response": Has content

### Test 3: UI Test

1. http://localhost:3001 → Chat/Tasks
2. Select "gemini-1.5-pro" model
3. Send: "What is your model name?"
4. Check response shows Google provider

---

## Troubleshooting Quick Links

**Problem** → **Solution** → **Document**

- "Gemini not in dropdown" → Check backend running → GEMINI_COMPLETE_REFERENCE.md §Debugging Step 2
- "Getting Claude response" → Check API key → GEMINI_COMPLETE_REFERENCE.md §Issue: Getting Claude
- "CORS error in browser" → Update ALLOWED_ORIGINS → GEMINI_COMPLETE_REFERENCE.md §Issue: CORS error
- "Slow response" → Try gemini-1.5-flash or Ollama → GEMINI_TEST_DEBUG_GUIDE.md §8. Performance
- "Rate limit" → Wait 1-2 min or use Ollama → GEMINI_COMPLETE_REFERENCE.md §Issue: Rate limit

---

## Next Steps

1. **Read the appropriate guide** based on your needs:
   - Quick start? → Read GEMINI_QUICK_TEST.md
   - Full reference? → Read GEMINI_COMPLETE_REFERENCE.md
   - Need debugging help? → Read GEMINI_TEST_DEBUG_GUIDE.md

2. **Run a quick test:**

   ```bash
   # Option A: Terminal
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"conversationId":"test","model":"gemini-1.5-pro","message":"test"}' | jq '.provider'

   # Option B: Script
   .\scripts\test-gemini.ps1  # Windows
   bash scripts/test-gemini.sh # Mac/Linux

   # Option C: UI
   Open http://localhost:3001 and test in chat
   ```

3. **Start using Gemini in Oversight Hub:**
   - Select Gemini model from dropdown
   - Send messages
   - Monitor response metadata
   - Check backend logs for detailed info

4. **Get help:**
   - Check the troubleshooting section in any guide
   - Review backend logs (terminal where backend is running)
   - Check browser DevTools (F12 → Network tab)

---

## Key Endpoints Reference

| What          | Endpoint                        | Command                                                                   |
| ------------- | ------------------------------- | ------------------------------------------------------------------------- |
| List models   | GET `/api/v1/models/available`  | `curl http://localhost:8000/api/v1/models/available`                      |
| Check status  | GET `/api/v1/models/status`     | `curl http://localhost:8000/api/v1/models/status`                         |
| Send message  | POST `/api/chat`                | See examples above                                                        |
| View history  | GET `/api/chat/history/{id}`    | `curl http://localhost:8000/api/chat/history/{conversation_id}`           |
| Clear history | DELETE `/api/chat/history/{id}` | `curl -X DELETE http://localhost:8000/api/chat/history/{conversation_id}` |
| System health | GET `/api/health`               | `curl http://localhost:8000/api/health`                                   |

---

## Support Resources

- **API Documentation:** http://localhost:8000/api/docs (when backend running)
- **Gemini API Key:** https://aistudio.google.com/app/apikey
- **Gemini Documentation:** https://ai.google.dev
- **Project Docs:** See `docs/` folder for architecture and setup

---

## Files Location

All documentation is in the repo root:

```
glad-labs-website/
├── GEMINI_QUICK_TEST.md          ← Start here for quick setup
├── GEMINI_COMPLETE_REFERENCE.md  ← Full API reference
├── GEMINI_TEST_DEBUG_GUIDE.md    ← Detailed debugging guide
└── scripts/
    ├── test-gemini.sh            ← Bash test script
    └── test-gemini.ps1           ← PowerShell test script
```

---

## Summary

You have everything needed to test and debug Google Gemini in your Oversight Hub:

- ✅ 3 comprehensive markdown guides
- ✅ 2 automated test scripts (Bash & PowerShell)
- ✅ 20+ working code examples
- ✅ Step-by-step debugging workflow
- ✅ Real-world testing scenarios

**Ready to test?** Start with one of these:

1. `GEMINI_QUICK_TEST.md` (5 minutes)
2. Terminal test above (30 seconds)
3. Run `scripts/test-gemini.ps1` or `bash scripts/test-gemini.sh` (2 minutes)

**Happy testing! 🚀**
