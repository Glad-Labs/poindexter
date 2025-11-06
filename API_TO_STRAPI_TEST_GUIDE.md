# 🔬 Complete API → Strapi Pipeline Test Guide

**Date:** November 6, 2025  
**Purpose:** End-to-end testing of blog generation pipeline from REST API to Strapi CMS publication

---

## 🎯 What This Test Does

The test validates the complete pipeline:

```
1. Health Checks
   ├─ Backend API running?
   └─ Strapi CMS accessible?

2. Create Task via API
   ├─ POST /api/tasks
   └─ Receive task ID

3. Monitor Execution
   ├─ Poll GET /api/tasks/{id}
   ├─ Watch status: pending → in_progress → completed
   └─ Measure execution time

4. Verify Content
   ├─ Check content generated (character count)
   ├─ Check quality score (0-100)
   └─ Verify approval (≥75 required)

5. Verify Strapi Publication
   ├─ Check blog post exists in Strapi
   ├─ Verify post data (title, slug, status)
   └─ Confirm content matches

6. Generate Report
   ├─ Show pass/fail for each step
   └─ Provide next troubleshooting steps
```

---

## 📋 Prerequisites

Before running the test, ensure you have:

### 1. Environment Variables Set

Create `.env.local` in project root:

```bash
# === REQUIRED: Strapi Publishing ===
STRAPI_URL=http://localhost:1337
STRAPI_API_TOKEN=your-token-here

# === REQUIRED: LLM (choose one) ===
USE_OLLAMA=true
# OR
# OPENAI_API_KEY=sk-...
# OR
# ANTHROPIC_API_KEY=sk-ant-...
```

**How to get Strapi token:**

1. Start Strapi: `cd cms/strapi-v5-backend; npm run develop`
2. Go to http://localhost:1337/admin
3. Settings (gear) → API Tokens → Create New
4. Name: "Blog Generation"
5. Copy token to `.env.local`

### 2. Services Running

Start each service in separate PowerShell terminal:

**Terminal 1: Strapi CMS**

```powershell
cd cms\strapi-v5-backend
npm run develop
# Should show: http://localhost:1337
```

**Terminal 2: FastAPI Backend**

```powershell
cd src\cofounder_agent
python -m uvicorn main:app --reload
# Should show: Application startup complete
```

**Terminal 3: Ollama (if using local AI)**

```powershell
ollama serve
# Should show: Listening on 127.0.0.1:11434
```

**Terminal 4: Test Script**

```powershell
# Wait for other services to be ready, then run:
.\test_api_to_strapi.ps1
```

---

## 🚀 Running the Test

### Option 1: Automated Test (Recommended)

```powershell
# From project root
.\test_api_to_strapi.ps1
```

**Expected Output:**

```
╔════════════════════════════════════════════════════════════════╗
║ API → STRAPI PIPELINE TEST                                    ║
╚════════════════════════════════════════════════════════════════╝

Configuration:
  Backend: http://localhost:8000
  Strapi:  http://localhost:1337
  Token:   ✅ Set

━━━ STEP 1 ━━━
Checking Backend Health
✅ Backend is running
ℹ️  Status: healthy

━━━ STEP 2 ━━━
Checking Strapi CMS
✅ Strapi is accessible
ℹ️  Current articles in database: 5

━━━ STEP 3 ━━━
Creating Test Task
ℹ️  Task payload:
...
✅ Task created successfully
ℹ️  Task ID: f47ac10b-58cc-4372-a567-0e02b2c3d479
ℹ️  Status: pending

━━━ STEP 4 ━━━
Monitoring Task Execution (up to 90 seconds)
ℹ️  [2s] Status: pending
ℹ️  [5s] Status: in_progress
ℹ️  [15s] Status: in_progress
✅ Task completed!

━━━ STEP 5 ━━━
Analyzing Task Result
ℹ️  Generated content: 1847 characters
✅ Content generated with good length
ℹ️  Quality score: 87/100
✅ Content approved (score: 87)
ℹ️  Strapi post ID: 5
ℹ️  Publish status: published

━━━ STEP 6 ━━━
Verifying Blog Post in Strapi Database
✅ Blog post found in Strapi!
ℹ️  Post ID: 5
ℹ️  Title: The Future of Artificial Intelligence in Business Automation
ℹ️  Slug: the-future-of-artificial-intelligence-in-business-automation
ℹ️  Status: published
ℹ️  Created: 2025-11-06T14:23:45Z
ℹ️  Content preview: # The Future of Artificial Intelligence in Business Automation

╔════════════════════════════════════════════════════════════════╗
║ TEST SUMMARY                                                   ║
╚════════════════════════════════════════════════════════════════╝

Results:
  ✅ Backend Health
  ✅ Strapi Connection
  ✅ Task Creation
  ✅ Task Execution
  ✅ Content Generation
  ✅ Quality Validation
  ✅ Strapi Publishing

Passed: 7/7

🎉 FULL PIPELINE TEST PASSED!

Your blog generation pipeline is working end-to-end:
  ✓ API created task
  ✓ Background executor processed it
  ✓ Orchestrator generated content
  ✓ Critique loop validated quality
  ✓ Strapi published blog post
```

### Option 2: Manual Testing (For Debugging)

**Step 1: Create a task**

```powershell
$task = @{
    task_name = "Manual Test"
    topic = "AI in Healthcare"
    primary_keyword = "AI, healthcare, diagnosis"
    target_audience = "Medical professionals"
    category = "healthcare"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/tasks" `
    -Method Post `
    -Body $task `
    -ContentType "application/json"
```

**Step 2: Check task status**

```powershell
# Replace {TASK_ID} with ID from Step 1
Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/{TASK_ID}"
```

**Step 3: Monitor until completed**

```powershell
# Run this in a loop until status = "completed"
$id = "{TASK_ID}"
while ($true) {
    $result = Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/$id"
    Write-Host "Status: $($result.status)" -ForegroundColor Cyan
    if ($result.status -eq "completed") {
        Write-Host "✅ Complete!" -ForegroundColor Green
        $result | Format-Table
        break
    }
    Start-Sleep -Seconds 2
}
```

**Step 4: Verify in Strapi**

```powershell
# Replace {TOKEN} with your STRAPI_API_TOKEN
$headers = @{ "Authorization" = "Bearer {TOKEN}" }
Invoke-RestMethod `
    -Uri "http://localhost:1337/api/articles" `
    -Headers $headers |
    Select-Object -ExpandProperty data |
    Sort-Object -Property createdAt -Descending |
    Select-Object -First 1 |
    Format-Table id, title, slug, status, createdAt
```

---

## ✅ What to Look For

### Successful Pipeline

- [ ] All 7 steps show ✅
- [ ] Task ID is a valid UUID
- [ ] Status transitions: pending → in_progress → completed
- [ ] Content length > 300 characters
- [ ] Quality score ≥ 75
- [ ] Publish status = "published"
- [ ] Strapi post ID is numeric
- [ ] Blog post appears in Strapi admin

### If Something Fails

**❌ Backend not responding**

- Check: Is FastAPI running?
- Fix: `cd src\cofounder_agent; python -m uvicorn main:app --reload`

**❌ Strapi connection failed**

- Check: Is Strapi running?
- Check: Is STRAPI_API_TOKEN set correctly?
- Fix: Restart Strapi, regenerate token, update .env.local

**❌ Task not completing**

- Check: Are logs showing "in_progress"?
- Check: TaskExecutor running in backend logs?
- Fix: Check backend terminal for error messages

**❌ Content quality too low**

- Score below 75?
- Check: Is orchestrator available?
- Check: Are LLM models working?
- Fix: Verify Ollama or API keys are set

**❌ Post not in Strapi**

- Published but not visible?
- Check: Is publish_status = "published"?
- Fix: Verify STRAPI_API_TOKEN has permissions

---

## 📊 Understanding the Results

### Task Execution Times

- **Normal:** 5-30 seconds
- **Slow:** 30-90 seconds (orchestrator processing)
- **Very Slow:** >90 seconds (possible LLM/network issue)

### Quality Score Interpretation

| Score  | Meaning   | Action                     |
| ------ | --------- | -------------------------- |
| 90-100 | Excellent | Publish immediately ✅     |
| 75-89  | Good      | Publish with confidence ✅ |
| 60-74  | Fair      | Attempt refinement ⚠️      |
| <60    | Poor      | Reject and retry ❌        |

### Content Generation Issues

| Problem            | Cause                  | Fix                  |
| ------------------ | ---------------------- | -------------------- |
| Very short content | Orchestrator failed    | Check LLM connection |
| Generic content    | Fallback template used | Verify orchestrator  |
| Quality score 0    | Critique loop error    | Check logs           |
| Not published      | Strapi token invalid   | Regenerate token     |

---

## 🔍 Debugging Commands

### Check Backend Health

```powershell
curl -Uri "http://localhost:8000/api/health" | ConvertFrom-Json | Format-Table
```

### Check Task Executor Stats

```powershell
curl -Uri "http://localhost:8000/api/tasks/stats" | ConvertFrom-Json | Format-Table
```

### Check All Tasks

```powershell
curl -Uri "http://localhost:8000/api/tasks?limit=10" | ConvertFrom-Json | Format-Table
```

### Check Strapi Articles

```powershell
$headers = @{ "Authorization" = "Bearer {TOKEN}" }
curl -Uri "http://localhost:1337/api/articles?pagination[limit]=5" `
     -Headers $headers | ConvertFrom-Json | Format-Table
```

### View Backend Logs

```powershell
# Terminal where backend is running - look for:
# - Task executor polling messages
# - Orchestrator generation logs
# - Critique loop validation
# - Strapi publishing results
```

---

## 🎯 Success Criteria

Your pipeline is working when:

- ✅ Test shows all 7 steps passing
- ✅ Tasks complete within 30 seconds
- ✅ Content quality scores ≥ 75
- ✅ New blog posts appear in Strapi
- ✅ Posts are marked "published"
- ✅ Backend logs show no errors

---

## 📝 Next Steps After Successful Test

1. **Create Multiple Tasks**
   - Test with different topics
   - Verify consistency

2. **Monitor Performance**
   - Track execution times
   - Monitor quality scores
   - Check error rates

3. **Test Public Site**
   - Start: `cd web/public-site; npm run dev`
   - Check: http://localhost:3000
   - Verify: Published posts appear

4. **Scale to Production**
   - Deploy backend to Railway
   - Deploy frontend to Vercel
   - Use production Strapi instance
   - Configure production environment

---

## 🆘 Getting Help

If test fails:

1. **Check logs first:**
   - Backend terminal (src/cofounder_agent)
   - Strapi terminal (cms/strapi-v5-backend)
   - Ollama terminal (if using)

2. **Verify prerequisites:**
   - All services running?
   - Environment variables set?
   - Strapi token valid?

3. **Run manual tests:**
   - Create task manually
   - Check each step individually
   - Identify exact failure point

4. **Check documentation:**
   - PRODUCTION_LAUNCH_GUIDE.md
   - Backend logs for specific errors
   - Strapi admin for publishing issues

---

**You're ready to test! Run the script and watch your pipeline work!** 🚀
