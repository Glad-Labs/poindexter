# 🎯 START HERE - Test Your Pipeline in 5 Minutes

**TL;DR:** Run one command and watch your blog post get created automatically.

---

## ⚡ The 30-Second Version

```powershell
# 1. Open Terminal and navigate to project
cd c:\Users\mattm\glad-labs-website

# 2. Make sure services are running (see below)
# Then run ONE of these:

# OPTION A: Guided test with checks (RECOMMENDED)
.\test_pipeline_quick.ps1

# OPTION B: Full automated test
.\test_api_to_strapi.ps1
```

---

## 🚀 Full Setup (5 minutes)

### Step 1: Open 4 Terminal Windows

You'll need 4 terminals running simultaneously.

### Terminal 1: Start Strapi CMS

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-v5-backend
npm run develop
```

**Wait for:** `Server is running at...` message

### Terminal 2: Start AI Backend

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload
```

**Wait for:** `Application startup complete` message

### Terminal 3: Start Ollama (Optional but Recommended)

```powershell
ollama serve
```

**Wait for:** Server listening message (if using Ollama for free LLM)

**Skip this if:** You're using OpenAI/Anthropic/Google API keys

### Terminal 4: Run the Test

```powershell
cd c:\Users\mattm\glad-labs-website

# First time? Use guided test
.\test_pipeline_quick.ps1

# Or go straight to full test
.\test_api_to_strapi.ps1
```

---

## 📋 Quick Checklist

Before running test:

- [ ] **Terminal 1**: Strapi running (port 1337)
- [ ] **Terminal 2**: Backend running (port 8000)
- [ ] **Terminal 3**: Ollama running (optional, port 11434)
- [ ] **Terminal 4**: Have Strapi token ready

### Get Your Strapi Token (2 minutes)

1. Open: http://localhost:1337/admin
2. Go to: **Settings** → **API Tokens**
3. **Create new API Token** or copy existing one
4. The token appears like: `abc123def456...`

### Add to Environment

Terminal 4, before running test:

```powershell
# Set environment variable
$env:STRAPI_API_TOKEN = "your-token-from-admin"

# Verify
echo $env:STRAPI_API_TOKEN
# Should show your token
```

**OR** edit `.env.local` in project root:

```
STRAPI_URL=http://localhost:1337
STRAPI_API_TOKEN=your-token-here
USE_OLLAMA=true
```

---

## ✅ Watch It Work

Run in Terminal 4:

```powershell
.\test_pipeline_quick.ps1
```

You'll see:

```
🔍 Checking prerequisites...
✅ .env.local found
✅ STRAPI_API_TOKEN is set
✅ Backend is running (http://localhost:8000)
✅ Strapi is running (http://localhost:1337)

🚀 Starting pipeline test...
✅ Task created successfully (ID: f47ac10b)
⏳ Monitoring execution...
✅ Task completed! (12 seconds)
✅ Content quality: 87/100 ✅
✅ Blog post published to Strapi!

🎉 SUCCESS! Full pipeline working!
```

---

## 🎉 What Just Happened?

Your system just:

1. Created a new task via API
2. Waited for backend to process it
3. Generated a full blog post (1500+ words)
4. Validated content quality
5. Published it to Strapi
6. Confirmed it in database

**Total time:** 5-30 seconds typically

---

## 📊 Results

After test passes, check your new blog post:

### In Strapi Admin

```
http://localhost:1337/admin/content-manager/collection-types/api::article.article
```

You should see your new post in the list

### Via API

```powershell
$headers = @{ "Authorization" = "Bearer YOUR_TOKEN" }
curl -Uri "http://localhost:1337/api/articles?sort=-createdAt&pagination[limit]=1" `
     -Headers $headers
```

Shows your newest blog post

### On Public Site (if running)

```
http://localhost:3000
```

Your blog post appears automatically

---

## 🐛 Something Not Working?

### Issue: "Cannot connect to backend"

**Fix:**

```powershell
# Terminal 2 - restart backend
Ctrl+C
python -m uvicorn main:app --reload
```

### Issue: "Strapi token invalid"

**Fix:**

```powershell
# Get new token from http://localhost:1337/admin
# Settings → API Tokens → Create new
# Copy exact token (including any dashes/special chars)
$env:STRAPI_API_TOKEN = "paste-token-here"
```

### Issue: "Task stuck in pending"

**Fix:**

```powershell
# Check Terminal 2 for error messages
# If database error: delete .tmp/data.db and restart
# If orchestrator error: verify LLM connection
```

### Issue: "Content quality too low"

**This is normal!** First runs often have score 75-89.

- System learns over time
- Future runs will improve
- Score 75+ means it's publishable

### Need More Help?

Check these files:

- **`TESTING_REFERENCE.md`** - Complete testing guide
- **`API_TO_STRAPI_TEST_GUIDE.md`** - Detailed steps
- **`TROUBLESHOOTING_PIPELINE.md`** - Fix common issues

---

## 🎓 Understanding the Pipeline

```
Your Command (✨ You are here)
        ↓
test_pipeline_quick.ps1 / test_api_to_strapi.ps1 (✨ Executes tests)
        ↓
Creates Task via REST API (✨ Task stored in database)
        ↓
TaskExecutor polls every 5 seconds (✨ Backend monitoring)
        ↓
Orchestrator generates content (✨ AI creates blog post)
        ↓
ContentCritiqueLoop validates quality (✨ QA scoring 0-100)
        ↓
StrapiPublisher posts to CMS (✨ Blog post appears)
        ↓
Database updated (✨ Everything saved)
        ↓
🎉 Blog Post LIVE!
```

---

## 🚀 Ready? Let's Go!

### Quick Test (Recommended First Time)

```powershell
.\test_pipeline_quick.ps1
```

### Full Test (When Confident)

```powershell
.\test_api_to_strapi.ps1
```

### Manual Testing (To Learn)

```
See: API_TO_STRAPI_TEST_GUIDE.md
```

---

## 📈 What's Next?

After test passes:

1. **Run 3-5 times** to verify consistency
2. **Check quality scores** improve over time
3. **Review generated content** - is it good?
4. **Check public site** - see posts live
5. **Review Strapi admin** - confirm posts there
6. **Then deploy to production!**

See: `PRODUCTION_LAUNCH_GUIDE.md`

---

## ⏱️ Expected Timing

| Activity       | Time             |
| -------------- | ---------------- |
| Start services | 2-3 minutes      |
| Run quick test | 1-2 minutes      |
| See blog post  | 5-30 seconds     |
| **Total**      | **8-35 minutes** |

---

**Everything working?** You're ready for production! 🚀

**Questions?** Check `TESTING_REFERENCE.md` or `TROUBLESHOOTING_PIPELINE.md`
