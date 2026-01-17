# Gemini Testing & Debugging - Documentation Index

Welcome! Everything is set up to test Google Gemini in your Oversight Hub. Here's where to find what you need.

## 📚 Documentation Files (In Order of Use)

### 1. 🚀 **START HERE** → [GEMINI_TESTING_SUMMARY.md](./GEMINI_TESTING_SUMMARY.md)

**Best for:** Getting oriented and understanding what's been set up  
**Read time:** 3-5 minutes  
**Contains:**

- Current status overview
- 3 quick testing options
- File location reference
- Getting started checklist

### 2. ⚡ **QUICK TEST** → [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)

**Best for:** Running tests in the next 5 minutes  
**Read time:** 5 minutes  
**Contains:**

- Step-by-step setup verification
- Terminal test examples
- UI testing steps
- Common quick fixes

### 3. 🔍 **DETAILED REFERENCE** → [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)

**Best for:** Complete API documentation and examples  
**Read time:** 10-15 minutes  
**Contains:**

- All API endpoints documented
- Request/response examples
- Fallback chain behavior
- Debugging workflow
- Performance tips
- Command reference

### 4. 🛠️ **DEBUGGING GUIDE** → [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)

**Best for:** When something isn't working  
**Read time:** 15-20 minutes  
**Contains:**

- 10 detailed test scenarios
- Common issues and solutions
- Advanced debugging techniques
- Network inspection guide
- Real-world testing scenarios
- Troubleshooting summary table

### 5. 🏗️ **ARCHITECTURE** → [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)

**Best for:** Understanding how everything works  
**Read time:** 10 minutes  
**Contains:**

- System architecture diagram
- Request flow sequence
- Fallback chain visualization
- Testing workflow diagram
- API hierarchy
- Performance expectations

---

## 🔧 Test Scripts

### PowerShell (Windows)

```powershell
# Run 10 automated tests
.\scripts\test-gemini.ps1
```

### Bash (Mac/Linux)

```bash
# Run 10 automated tests
bash scripts/test-gemini.sh
```

Both scripts output:

- ✅ PASS/FAIL for each test
- 📊 Success rate percentage
- ⏱️ Performance metrics
- 🎯 Detailed results

---

## 🎯 Quick Navigation by Need

### "I want to test Gemini right now"

1. Read: [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md) (5 min)
2. Run: `.\scripts\test-gemini.ps1` (2 min) or `bash scripts/test-gemini.sh` (2 min)
3. Open: http://localhost:3001 and test in UI (2 min)

**Total: 9 minutes**

---

### "I need API documentation and examples"

1. Read: [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)
2. Look for specific endpoint section
3. Copy example cURL command
4. Test in terminal

**Best sections:**

- Section 1: Get Available Models
- Section 2: Check Provider Status
- Section 3: Send Chat Message
- Section 4: Get Conversation History

---

### "Something isn't working"

1. Read: [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)
2. Go to: Section 4 - Common Issues
3. Find your symptom
4. Follow solution steps

**Quick links to issues:**

- "Gemini not appearing in model list" → Search docs for "not appearing"
- "Getting Claude response instead of Gemini" → Search for "wrong provider"
- "CORS error in browser" → Search for "CORS error"
- "Rate limit errors" → Search for "Rate limit"

---

### "I want to understand the architecture"

1. Read: [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)
2. Study the flow diagrams
3. Review the sequence charts
4. Check performance expectations

---

## 📋 Status Checklist

Your Gemini setup is **READY TO USE**:

- ✅ Google API Key configured in `.env.local`
- ✅ Backend running on http://localhost:8000
- ✅ Oversight Hub running on http://localhost:3001
- ✅ Gemini models available (gemini-1.5-pro, gemini-1.5-flash, etc.)
- ✅ Automatic fallback chain enabled
- ✅ PostgreSQL database connected
- ✅ 5 documentation files created
- ✅ 2 test scripts created (PowerShell + Bash)

---

## 🚀 Getting Started (Choose One Path)

### Path 1: I'm in a hurry (5 minutes)

1. Quick terminal test:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversationId":"test","model":"gemini-1.5-pro","message":"hello"}' | jq '.provider'
# Expected: "google"
```

2. Read: [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)

---

### Path 2: I want thorough documentation (15 minutes)

1. Read: [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)
2. Run: `.\scripts\test-gemini.ps1` or `bash scripts/test-gemini.sh`
3. Try UI test at http://localhost:3001

---

### Path 3: I want to understand everything (30 minutes)

1. Read: [GEMINI_TESTING_SUMMARY.md](./GEMINI_TESTING_SUMMARY.md)
2. Read: [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)
3. Read: [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)
4. Run both test scripts
5. Read: [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)

---

## 💡 Key Concepts

### Model Selection

When you select "gemini-1.5-pro" in the dropdown:

1. Backend validates the model
2. Loads your Google API key
3. Routes request to Gemini API
4. Returns response with `"provider": "google"`

### Automatic Fallback

If Gemini isn't available:

1. System tries HuggingFace (free)
2. Then tries Claude (paid)
3. Then tries GPT-4 (expensive)
4. Response shows which provider was used

### Persistence

All conversations are saved to PostgreSQL:

- Conversation ID
- Messages (user + assistant)
- Model used
- Provider used
- Tokens consumed
- Cost estimate
- Timestamp

---

## 🔗 External Resources

- **Gemini API Key:** https://aistudio.google.com/app/apikey
- **Gemini Documentation:** https://ai.google.dev
- **API Interactive Docs:** http://localhost:8000/api/docs (when backend running)
- **Glad Labs Docs:** See `docs/` folder in repo

---

## 📞 Support Resources

### Common Questions

**Q: Where is my Gemini API key?**  
A: It's in `.env.local` file. Check: `grep GOOGLE_API_KEY .env.local`

**Q: How do I know if Gemini is working?**  
A: Check response has `"provider": "google"` in it.

**Q: Why am I getting Claude response?**  
A: Your API key might be invalid. Get new one at https://aistudio.google.com/app/apikey

**Q: How much does Gemini cost?**  
A: ~$0.31/month for typical usage. Check pricing in [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)

**Q: What if I want to use Ollama (free)?**  
A: Set in `.env.local`: `USE_OLLAMA=true` and restart backend

**Q: How do I run the test script?**  
A:

- Windows: `.\scripts\test-gemini.ps1`
- Mac/Linux: `bash scripts/test-gemini.sh`

---

## 📁 File Organization

```
Repository Root (glad-labs-website/)
├── GEMINI_TESTING_SUMMARY.md      ← Start here for overview
├── GEMINI_QUICK_TEST.md           ← 5-minute quick start
├── GEMINI_COMPLETE_REFERENCE.md   ← Full API reference
├── GEMINI_TEST_DEBUG_GUIDE.md     ← Debugging help
├── GEMINI_ARCHITECTURE.md         ← Architecture & diagrams
├── GEMINI_TESTING_INDEX.md        ← This file
│
├── scripts/
│   ├── test-gemini.ps1            ← PowerShell test (Windows)
│   └── test-gemini.sh             ← Bash test (Mac/Linux)
│
├── .env.local                     ← Your configuration
│                                   (GOOGLE_API_KEY here)
│
└── src/cofounder_agent/
    ├── main.py                    ← Backend entry point
    ├── routes/
    │   ├── chat_routes.py         ← Chat endpoints
    │   └── model_routes.py        ← Model/provider endpoints
    │
    └── services/
        ├── model_router.py        ← Model selection logic
        ├── model_consolidation_service.py  ← Provider management
        └── database_service.py    ← PostgreSQL persistence
```

---

## ⏱️ Typical Workflow

### First Time Setup (Total: 15 minutes)

1. Read this file (GEMINI_TESTING_INDEX.md) - 2 min
2. Read GEMINI_QUICK_TEST.md - 5 min
3. Run test script - 3 min
4. Test in UI at http://localhost:3001 - 5 min

### Regular Usage

1. Open Oversight Hub: http://localhost:3001
2. Select "gemini-1.5-pro" from model dropdown
3. Send your message
4. See response with Gemini

### When Debugging Issues

1. Identify the symptom
2. Go to GEMINI_TEST_DEBUG_GUIDE.md
3. Search for your issue
4. Follow solution steps
5. Verify with test scripts

---

## ✨ Next Steps

1. **If you haven't read anything yet:**
   → Start with [GEMINI_TESTING_SUMMARY.md](./GEMINI_TESTING_SUMMARY.md)

2. **If you want to test right now:**
   → Go to [GEMINI_QUICK_TEST.md](./GEMINI_QUICK_TEST.md)

3. **If you need API examples:**
   → Check [GEMINI_COMPLETE_REFERENCE.md](./GEMINI_COMPLETE_REFERENCE.md)

4. **If something isn't working:**
   → Read [GEMINI_TEST_DEBUG_GUIDE.md](./GEMINI_TEST_DEBUG_GUIDE.md)

5. **If you want to understand the architecture:**
   → Study [GEMINI_ARCHITECTURE.md](./GEMINI_ARCHITECTURE.md)

---

## 🎉 Ready?

**Your Gemini setup is complete and tested. You can now:**

✅ Use Gemini in Oversight Hub  
✅ Send messages and get responses  
✅ View conversation history  
✅ Monitor model and provider in metadata  
✅ Use automatic fallback to other models  
✅ Run your own tests and debug

---

**Pick a document above and get started!**

---

_Last Updated: January 16, 2026_  
_Status: ✅ Ready for Use_  
_Backend: http://localhost:8000_  
_Frontend: http://localhost:3001_
