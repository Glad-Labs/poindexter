# 🎯 Testing Your API → Strapi Pipeline: Complete Reference

**Created:** November 6, 2025  
**Status:** Ready to Execute  
**Time to First Test:** 2 minutes

---

## 📦 What You Got

| File                          | Purpose                                | Time      |
| ----------------------------- | -------------------------------------- | --------- |
| `test_pipeline_quick.ps1`     | Quick-start with prerequisite checking | 5 min     |
| `test_api_to_strapi.ps1`      | Full automated end-to-end test         | 10 min    |
| `API_TO_STRAPI_TEST_GUIDE.md` | Comprehensive guide with manual steps  | Reference |
| `TROUBLESHOOTING_PIPELINE.md` | Fix common issues                      | Reference |

---

## 🚀 Quick Start (Choose One)

### Option A: Guided Quick Test ⭐ RECOMMENDED

```powershell
.\test_pipeline_quick.ps1
```

**What it does:**

1. Checks if `.env.local` exists
2. Verifies `STRAPI_API_TOKEN` is set
3. Tests backend health (port 8000)
4. Tests Strapi health (port 1337)
5. Shows setup instructions if anything missing
6. Runs test and displays results

**Time:** ~5-10 minutes (including any setup needed)

**Best for:** First-time testing, quick validation

---

### Option B: Full Automated Test

```powershell
.\test_api_to_strapi.ps1
```

**What it does:**

1. Create blog task via API
2. Monitor execution (polls every 3 seconds)
3. Verify content generation
4. Check quality validation
5. Confirm Strapi publication
6. Display comprehensive results

**Time:** ~5-30 minutes depending on LLM speed

**Best for:** Complete validation, production-ready checks

---

### Option C: Manual Step-by-Step

Follow: `API_TO_STRAPI_TEST_GUIDE.md`

**What it does:**

- Run each curl command individually
- See exactly what API returns
- Debug specific issues

**Time:** ~15-20 minutes

**Best for:** Learning, debugging, detailed understanding

---

## 📋 Prerequisites Checklist

Before running ANY test:

### Services Must Be Running

- [ ] **Strapi CMS** running on port 1337

  ```powershell
  cd cms\strapi-v5-backend
  npm run develop
  ```

- [ ] **Backend API** running on port 8000

  ```powershell
  cd src\cofounder_agent
  python -m uvicorn main:app --reload
  ```

- [ ] **Ollama** (if using local LLM)
  ```powershell
  ollama serve
  ```

### Environment Variables

- [ ] `.env.local` exists in project root
- [ ] `STRAPI_API_TOKEN` is set (from Strapi admin)
- [ ] `STRAPI_URL` set to `http://localhost:1337`
- [ ] `USE_OLLAMA=true` OR valid `OPENAI_API_KEY` set

### Quick Check

```powershell
# Test backend is running
curl -Uri "http://localhost:8000/api/health"
# Should see: {"status":"healthy"...}

# Test Strapi is running
curl -Uri "http://localhost:1337/admin"
# Should see: HTML page loads
```

---

## 🎬 Running the Test: Step-by-Step

### Step 1: Set Up Environment

```powershell
# Navigate to project root
cd c:\Users\mattm\glad-labs-website

# Verify .env.local has your Strapi token
# Should include:
# STRAPI_URL=http://localhost:1337
# STRAPI_API_TOKEN=your-token-from-strapi-admin
```

**Get your Strapi token:**

1. Go to http://localhost:1337/admin
2. Settings → API Tokens
3. Create new or copy existing token
4. Add to `.env.local`:
   ```
   STRAPI_API_TOKEN=your-token-here
   ```

### Step 2: Start Services (4 Terminals)

**Terminal 1 - Strapi:**

```powershell
cd cms\strapi-v5-backend
npm run develop
# Wait for: "Server is running at..."
```

**Terminal 2 - Backend:**

```powershell
cd src\cofounder_agent
python -m uvicorn main:app --reload
# Wait for: "Application startup complete"
```

**Terminal 3 - Ollama (optional):**

```powershell
ollama serve
# Wait for: "Loaded model..." or starts listening
```

**Terminal 4 - Run Test:**

```powershell
# Navigate to project root
cd c:\Users\mattm\glad-labs-website

# Run quick test
.\test_pipeline_quick.ps1

# OR run full test
.\test_api_to_strapi.ps1
```

### Step 3: Watch the Results

The test will show:

```
✅ Backend is running
✅ Strapi is accessible
✅ Task created successfully (ID: f47ac10b-58cc...)
⏳ Waiting for task completion... (check 5/30)
✅ Task completed! (took 12 seconds)
✅ Content generated with good length (1847 chars)
✅ Content approved (score: 87/100)
✅ Blog post found in Strapi!

🎉 FULL PIPELINE TEST PASSED! (7/7 checks)

📊 Summary:
   - Content Quality: 87/100 ✅
   - Execution Time: 12 seconds
   - Blog Post ID: 47
   - Published URL: http://localhost:1337/api/articles/47
```

---

## ✅ Success Looks Like

After test passes:

1. **Check Strapi directly:**

   ```powershell
   curl -Uri "http://localhost:1337/api/articles" `
       -Headers @{"Authorization"="Bearer YOUR_TOKEN"}
   ```

   Should show your new blog post in the list

2. **Check Public Site** (if running):

   ```
   http://localhost:3000
   ```

   Should show new blog post featured

3. **Run test multiple times:**
   ```powershell
   .\test_api_to_strapi.ps1
   ```
   Each run should create a new blog post

---

## ⚠️ Common Issues & Fixes

### "Cannot connect to http://localhost:8000"

**Fix:**

```powershell
# Terminal 2: Start backend
cd src\cofounder_agent
python -m uvicorn main:app --reload
```

### "401 Unauthorized" from Strapi

**Fix:**

```powershell
# Get valid token from Strapi admin
# http://localhost:1337/admin → Settings → API Tokens
# Add to .env.local:
STRAPI_API_TOKEN=your-new-token
```

### Task stuck in "pending"

**Fix:**

```powershell
# Check backend is running (should see messages in Terminal 2)
# Check logs for "TaskExecutor" or "orchestrator" errors
# Restart backend (Ctrl+C, then rerun)
```

### "Content quality score too low"

- This is normal for first attempts (system learning)
- If consistent (<60): Check LLM is responsive
- If occasional (75-89): This is normal, will improve

### Test timeout (>90 seconds)

**Causes:**

- First run with Ollama (loads model from disk, ~60 sec)
- Network latency to cloud LLM
- System under heavy load

**Fix:**

- Wait for Ollama to finish loading
- Run again (second run faster)
- Check internet connection

---

## 📊 What Gets Tested

Each test validates:

| Check                  | Tests                           | Pass Means                            |
| ---------------------- | ------------------------------- | ------------------------------------- |
| **Backend Health**     | `/api/health` responds          | Backend is running                    |
| **Strapi Connection**  | `/api/articles` with token      | Strapi accessible, token valid        |
| **Task Creation**      | `POST /api/tasks` returns ID    | API working, database saving          |
| **Task Execution**     | Status changes from pending     | TaskExecutor running, polling working |
| **Content Generation** | content_length > 300 chars      | Orchestrator generating content       |
| **Quality Validation** | quality_score ≥ 75              | CritiqueLoop approving content        |
| **Strapi Publishing**  | Post appears in `/api/articles` | StrapiPublisher posting successfully  |

---

## 📈 Performance Baselines

Normal timing:

- First run: 30-60 seconds (includes model loading)
- Subsequent runs: 10-30 seconds
- Each phase: 1-5 seconds average

If consistently slow (>60 sec):

- Check Ollama performance
- Verify network connection
- Check database queries

---

## 🔍 Detailed Testing

### Test 1: Manual Health Check

```powershell
# Backend health
curl -Uri "http://localhost:8000/api/health"

# Strapi health
curl -Uri "http://localhost:1337/admin"

# Both should respond quickly without errors
```

### Test 2: Create Single Task

```powershell
$task = @{
    task_name = "Manual Test"
    topic = "AI in Business"
    primary_keyword = "artificial intelligence"
    target_audience = "Business leaders"
    category = "Technology"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/tasks" `
    -Method Post `
    -Body $task `
    -ContentType "application/json"

$response | Format-List
# Should show: id, status (pending), created_at
```

### Test 3: Monitor Task Execution

```powershell
$taskId = "YOUR_TASK_ID"

while ($true) {
    $task = Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/$taskId"

    Write-Host "Status: $($task.status)"

    if ($task.status -eq "completed") {
        Write-Host "✅ Task Complete!"
        $task | Select-Object content_length, quality_score, strapi_post_id | Format-List
        break
    }

    Start-Sleep -Seconds 3
}
```

### Test 4: Verify in Strapi

```powershell
$headers = @{ "Authorization" = "Bearer YOUR_TOKEN" }

# Get the blog post
curl -Uri "http://localhost:1337/api/articles/POST_ID" `
     -Headers $headers |
     ConvertFrom-Json |
     Select-Object @{n="Title";e={$_.data.title}}, `
                   @{n="Content Length";e={$_.data.content.Length}}, `
                   @{n="Published";e={$_.data.publishedAt}}
```

---

## 🎓 Next Steps After Passing

1. **Run test 3-5 times** to verify consistency
2. **Check public site** (if running) shows new posts
3. **Monitor executor stats:**
   ```powershell
   curl -Uri "http://localhost:8000/api/tasks/stats"
   ```
4. **Review logs** in all terminals for warnings
5. **Ready for production!** → Follow PRODUCTION_LAUNCH_GUIDE.md

---

## 📞 Get Help

**For detailed troubleshooting:**

- Check: `TROUBLESHOOTING_PIPELINE.md`
- Common issues with specific fixes
- Performance tuning guide
- Reset everything instructions

**For step-by-step manual testing:**

- Check: `API_TO_STRAPI_TEST_GUIDE.md`
- Each curl command explained
- Expected output for each step
- Debugging individual components

**For setup issues:**

- Check: `PRODUCTION_LAUNCH_GUIDE.md`
- Environment configuration
- Service startup
- Database setup

---

## 🎯 Success Criteria

Pipeline is working if ALL pass:

- ✅ `test_pipeline_quick.ps1` shows 7/7 checks passing
- ✅ OR `test_api_to_strapi.ps1` completes without errors
- ✅ AND blog post appears in Strapi within 30 seconds
- ✅ AND quality score is 75+ (≥75 = good, 90+ = excellent)
- ✅ AND manual curl confirms post in database

---

## 🚀 Ready?

```powershell
# Start in Terminal 4 after services running in Terminals 1-3:
.\test_pipeline_quick.ps1

# Watch it create a blog post end-to-end! 🎉
```

---

**Questions?** Review the guide files or check backend/Strapi logs for detailed errors.

**All Tests Passing?** → You're ready for production deployment!
