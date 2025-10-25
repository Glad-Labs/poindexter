# ⚡ Quick Start: Content Generation Workflow

**Goal:** Get blog posts generated with local Ollama and saved to Strapi in 10 minutes

---

## 🚀 Start All Services

Open 4 PowerShell terminals and run these (copy-paste):

### Terminal 1: Ollama

```powershell
ollama serve
```

✅ Should say "Listening on" after model loads

### Terminal 2: Strapi CMS

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-v5-backend; npm run develop
```

✅ Visit http://localhost:1337/admin

### Terminal 3: Backend API

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent; python -m uvicorn main:app --reload
```

✅ Visit http://localhost:8000/docs

### Terminal 4: Test Script

```powershell
cd c:\Users\mattm\glad-labs-website

# Get your Strapi token from http://localhost:1337/admin
# Settings > API Tokens > Create > Copy token

$env:STRAPI_API_TOKEN = "paste-your-token-here"

# Run test
.\scripts\test-e2e-workflow.ps1
```

---

## 📋 What Happens

1. ✅ Test verifies all services running
2. ✅ Generates blog post with Ollama (1-3 minutes)
3. ✅ Saves to Strapi CMS
4. ✅ Reports success

---

## ✅ Success = You See This

```
================================================
  ✅ E2E WORKFLOW TEST PASSED!
================================================
```

Then:

- Check http://localhost:1337/admin → Content Manager → Posts
- Check http://localhost:3000 → Your post on homepage

---

## 🔑 Get Strapi API Token

1. Open http://localhost:1337/admin
2. Settings (⚙️) → API Tokens
3. "Create new API token"
4. Name: "GLAD Labs"
5. Type: "Full access"
6. Create
7. Copy the token
8. Paste in Terminal 4 above

---

## 🎯 Next Steps After Success

1. **Build React UI** - Add ContentGenerator.jsx to Oversight Hub
2. **Click buttons instead of running scripts** - UI component will make it easy
3. **Generate posts on demand** - Full workflow from dashboard

---

## 🐛 Having Issues?

See detailed troubleshooting:

- `docs/QUICK_TEST_E2E_WORKFLOW.md` - Test guide with curl commands
- `docs/IMPLEMENTATION_GUIDE_E2E_WORKFLOW.md` - Full reference
- `docs/PHASE_6_STATUS.md` - Complete implementation details

---

**Estimated Time:** 10-15 minutes  
**Difficulty:** Easy  
**Status:** ✅ Ready to run
