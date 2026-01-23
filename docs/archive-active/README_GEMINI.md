# 🧪 Google Gemini Testing & Debugging for Oversight Hub

> Complete testing setup for Google Gemini integration in your Glad Labs AI system

## ⚡ Quick Start (Choose Your Path)

### 🏃 I have 5 minutes

```bash
# Test 1: Check Gemini is available
curl -s http://localhost:8000/api/v1/models/available | jq '.models[] | select(.provider=="google") | .name'

# Test 2: Send a chat message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversationId":"test","model":"gemini-1.5-pro","message":"hello"}' | jq '.provider'
# Expected output: "google"

# Test 3: Open UI
# Go to http://localhost:3001 and test in chat interface
```

### 🚶 I have 15 minutes

1. Read: **[GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md)** (2 min)
2. Read: **[GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)** (5 min)
3. Run test script (3 min):

   ```bash
   # Windows
   .\scripts\test-gemini.ps1

   # Mac/Linux
   bash scripts/test-gemini.sh
   ```

4. Test in UI (5 min)

### 🧑‍💼 I want full documentation

1. **[GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md)** - Navigation guide (start here!)
2. **[GEMINI_TESTING_SUMMARY.md](./GEMINI_TESTING_SUMMARY.md)** - Overview
3. **[GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)** - Full API reference
4. **[GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)** - Architecture & diagrams
5. **[GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)** - Debugging help

---

## 📚 Documentation Files

| File                                                               | Purpose                      | Read Time |
| ------------------------------------------------------------------ | ---------------------------- | --------- |
| **[GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md)**           | Navigation hub (START HERE!) | 3 min     |
| **[GEMINI_TESTING_SUMMARY.md](./GEMINI_TESTING_SUMMARY.md)**       | Quick overview & checklist   | 5 min     |
| **[GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)**                 | 5-minute quick start         | 5 min     |
| **[GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)** | Full API documentation       | 15 min    |
| **[GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)**     | Troubleshooting & debugging  | 20 min    |
| **[GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)**             | Architecture & flow diagrams | 10 min    |

---

## 🛠️ Test Scripts

### Automated Testing

**Windows (PowerShell):**

```powershell
.\scripts\test-gemini.ps1
```

**Mac/Linux (Bash):**

```bash
bash scripts/test-gemini.sh
```

**What they test:**

- ✅ Environment configuration
- ✅ Backend connectivity
- ✅ Models available
- ✅ Provider status
- ✅ Simple chat message
- ✅ Conversation history
- ✅ Complex message processing
- ✅ Error handling
- ✅ Performance metrics
- ✅ Fallback chain

---

## ✅ System Status

**Current Configuration:**

- ✅ Google API Key: **Configured** in `.env.local`
- ✅ Backend: **Running** on http://localhost:8000
- ✅ Oversight Hub: **Running** on http://localhost:3001
- ✅ Gemini Models: **Available**
  - gemini-1.5-pro (recommended)
  - gemini-1.5-flash (faster)
  - gemini-pro (legacy)
  - gemini-pro-vision (multimodal)
- ✅ Automatic Fallback: **Enabled**
- ✅ Database: **Connected**

---

## 🚀 Testing in 3 Steps

### Step 1: Verify Setup (30 seconds)

```bash
# Check Gemini API key
echo $GOOGLE_API_KEY
# Should output: AIzaSy... (your key)

# Check backend running
curl -s http://localhost:8000/api/health | jq .status
# Expected: "healthy" or "starting"

# Check Gemini available
curl -s http://localhost:8000/api/v1/models/available | jq '.models | length'
# Expected: > 0
```

### Step 2: Test Chat API (2 minutes)

```bash
# Send test message to Gemini
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "test-'$(date +%s)'",
    "model": "gemini-1.5-pro",
    "message": "What is your model name?"
  }' | jq '.'

# Check the response:
# - "provider": "google" ✓
# - "model": "gemini-1.5-pro" ✓
# - "response": Has content ✓
```

### Step 3: Test in UI (2-3 minutes)

1. Open: http://localhost:3001
2. Navigate to Chat/Tasks
3. Select "gemini-1.5-pro" from model dropdown (look for ☁️ icon)
4. Send: "What is your model name?"
5. Verify response shows provider: "google"

---

## 🔧 API Endpoints Reference

```bash
# Get available models
curl http://localhost:8000/api/v1/models/available

# Check provider status
curl http://localhost:8000/api/v1/models/status

# Send chat message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversationId": "unique-id",
    "model": "gemini-1.5-pro",
    "message": "Your message"
  }'

# Get conversation history
curl http://localhost:8000/api/chat/history/unique-id

# Clear conversation
curl -X DELETE http://localhost:8000/api/chat/history/unique-id

# System health
curl http://localhost:8000/api/health
```

---

## ⚠️ Troubleshooting Quick Links

| Problem                 | Solution                   | Doc                                                                                                                    |
| ----------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Gemini not in dropdown  | Check backend running      | [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md#problem-gemini-not-appearing-in-model-list)                  |
| Getting Claude response | Check API key              | [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md#problem-gemini-selected-but-getting-fallback-model-response) |
| CORS error              | Update ALLOWED_ORIGINS     | [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md#problem-cors-error-in-browser-console)                       |
| Rate limit              | Wait 1-2 min or use Ollama | [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md#problem-rate-limit-errors-429-status)                        |
| Slow response           | Try gemini-1.5-flash       | [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md#performance-expectations)                                            |

See **[GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)** for full troubleshooting section.

---

## 📋 Success Checklist

After setup, verify:

- [ ] Backend running: `curl http://localhost:8000/api/health`
- [ ] GOOGLE_API_KEY set: `echo $GOOGLE_API_KEY`
- [ ] Models available: `curl http://localhost:8000/api/v1/models/available | jq .total`
- [ ] Gemini in list: Shows gemini-1.5-pro, gemini-1.5-flash, etc.
- [ ] Chat works: Returns `"provider": "google"`
- [ ] Oversight Hub loads: http://localhost:3001 (no errors)
- [ ] Model dropdown shows Gemini
- [ ] Can send message and get response

---

## 💡 Key Features

**Automatic Model Fallback:**
If Gemini isn't available, system automatically tries:

1. 🖥️ Ollama (local, free, instant)
2. 🌐 HuggingFace (cheap)
3. ☁️ Gemini (good value)
4. 🧠 Claude (excellent)
5. ⚡ GPT-4 (premium)

**Conversation Persistence:**

- All messages stored in PostgreSQL
- Full conversation history available
- Metadata: model, provider, tokens, cost, timestamp

**Performance:**

- Response time: 1-3 seconds typically
- Monthly cost: ~$0.31 for typical usage
- Token tracking: Automatic cost calculation

---

## 🎯 Documentation Strategy

**First time?** → Read [GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md) (the navigation hub)

**Quick test?** → Use [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md) (5 minutes)

**Need API docs?** → Check [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)

**Having issues?** → See [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)

**Want diagrams?** → Study [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)

---

## 📞 Support Resources

- **Get Gemini API Key:** https://aistudio.google.com/app/apikey
- **Gemini API Docs:** https://ai.google.dev
- **Interactive API Docs:** http://localhost:8000/api/docs
- **Glad Labs Docs:** See `docs/` folder

---

## 🎉 Ready to Test?

### Option 1: Quick Terminal Test (30 seconds)

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversationId":"quick-test","model":"gemini-1.5-pro","message":"test"}' | jq '.provider'
# Expected: "google"
```

### Option 2: Run Automated Tests (2 minutes)

```bash
# Windows
.\scripts\test-gemini.ps1

# Mac/Linux
bash scripts/test-gemini.sh
```

### Option 3: Read Documentation (15 minutes)

Start with **[GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md)**

---

## 📁 Files Created for You

```
Repository Root/
├── GEMINI_TESTING_INDEX.md          ← Navigation hub (start here!)
├── GEMINI_TESTING_SUMMARY.md        ← Overview
├── GEMINI_QUICK_TEST.md             ← 5-minute quick start
├── GEMINI_COMPLETE_REFERENCE.md     ← Full API reference
├── GEMINI_TEST_DEBUG_GUIDE.md       ← Debugging guide
├── GEMINI_ARCHITECTURE.md           ← Architecture diagrams
├── README_GEMINI.md                 ← This file
│
└── scripts/
    ├── test-gemini.ps1              ← Windows test script
    └── test-gemini.sh               ← Mac/Linux test script
```

---

## ✨ What You Can Do Now

✅ Test Gemini in Oversight Hub  
✅ Send chat messages with Gemini  
✅ View conversation history  
✅ Monitor model and provider metadata  
✅ Automatic fallback to other models  
✅ Run automated tests  
✅ Debug any issues  
✅ Understand the architecture

---

## 🏁 Next Step

**Pick one:**

1. **Fast:** Run `.\scripts\test-gemini.ps1` or `bash scripts/test-gemini.sh` (2 min)
2. **Quick:** Read [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md) (5 min)
3. **Complete:** Read [GEMINI_TESTING_INDEX.md](./GEMINI_TESTING_INDEX.md) (3 min)

---

**Status:** ✅ Ready to Test  
**Backend:** http://localhost:8000  
**Frontend:** http://localhost:3001  
**Last Updated:** January 16, 2026

**Good luck! 🚀**
