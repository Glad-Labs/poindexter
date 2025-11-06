# 🚀 PRODUCTION LAUNCH GUIDE - Blog Generation Pipeline

**Status:** ✅ PRODUCTION READY  
**Date:** November 6, 2025  
**Version:** 3.0 (Production)

---

## 📋 Executive Summary

Your blog generation system is now **production-ready** with a complete end-to-end automated pipeline:

```
1. Create Task (API)
   ↓
2. Background Executor (5-second polling)
   ↓
3. Orchestrator generates content (with agents)
   ↓
4. Critique Loop validates quality
   ↓
5. Auto-publish to Strapi CMS
   ↓
6. Blog post goes LIVE 🎉
```

**Key Stats:**

- ✅ Content generation via orchestrator (multi-agent pipeline)
- ✅ Quality validation via critique loop (75+ score required)
- ✅ Automatic Strapi publishing (posts appear instantly on blog)
- ✅ Graceful error handling and fallbacks
- ✅ Full monitoring and logging

---

## 🔧 REQUIRED SETUP (Before Production Launch)

### Step 1: Set Environment Variables

Create or update your `.env.local` file in the project root:

```bash
# === Strapi Configuration (REQUIRED FOR PUBLISHING) ===
STRAPI_URL=http://localhost:1337              # Your Strapi instance
STRAPI_API_TOKEN=your-strapi-api-token-here   # Get from Strapi Admin → Settings → API Tokens

# === LLM Provider (Choose at least ONE) ===
# Option A: Local Ollama (FREE, Recommended for dev/testing)
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral                           # or llama3.2, neural-chat, etc.

# Option B: OpenAI (if no Ollama)
# OPENAI_API_KEY=sk-your-key-here

# Option C: Anthropic Claude
# ANTHROPIC_API_KEY=sk-ant-your-key-here

# Option D: Google Gemini
# GOOGLE_API_KEY=AIza-your-key-here

# === Application Config ===
ENVIRONMENT=production
DEBUG=false
API_BASE_URL=http://localhost:8000            # Backend URL
DATABASE_URL=postgresql://user:pass@localhost/glad_labs  # PostgreSQL connection
```

### Step 2: Get Strapi API Token

1. **Start Strapi CMS:**

   ```powershell
   cd cms/strapi-v5-backend
   npm run develop
   ```

2. **Create API Token in Strapi:**
   - Go to: http://localhost:1337/admin
   - Click Settings (gear icon) → Global Settings → API Tokens
   - Click "Create new API Token"
   - **Name:** "Blog Generation"
   - **Type:** Full access (for testing) or Custom (for production)
   - **Click Save**
   - **Copy the token** and paste into `.env.local` as `STRAPI_API_TOKEN`

### Step 3: Verify All Services Running

Open 4 terminals and start each service:

**Terminal 1: Strapi CMS**

```powershell
cd cms/strapi-v5-backend
npm run develop
# Should show: http://localhost:1337/admin
```

**Terminal 2: FastAPI Backend**

```powershell
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000
# Should show: http://localhost:8000/docs
```

**Terminal 3: Ollama (if using local AI)**

```powershell
ollama serve
# Should show: Loading model... Ready on http://localhost:11434
```

**Terminal 4: (Optional) Public Site**

```powershell
cd web/public-site
npm run dev
# Should show: http://localhost:3000
```

---

## 🎯 PRODUCTION WORKFLOW (How to Use)

### Method 1: Via REST API (Recommended for Production)

**Create a blog post task:**

```bash
POST http://localhost:8000/api/tasks
Content-Type: application/json

{
  "task_name": "Generate Blog Post",
  "topic": "AI in Business: Practical Applications",
  "primary_keyword": "AI business automation",
  "target_audience": "business leaders",
  "category": "AI & Technology"
}
```

**Monitor task progress:**

```bash
# Get task status
GET http://localhost:8000/api/tasks/{task_id}

# Expected response (completed):
{
  "task_id": "abc-123-def",
  "status": "completed",
  "topic": "AI in Business: Practical Applications",

  "content_length": 1847,
  "quality_score": 87,
  "content_approved": true,

  "strapi_post_id": "5",
  "strapi_url": "http://localhost:1337/api/articles/ai-in-business-practical-applications",
  "publish_status": "published",

  "pipeline_summary": {
    "phase_1_generation": "✅",
    "phase_2_critique": "✅ (87/100)",
    "phase_3_published": "✅ (ID: 5)"
  }
}
```

### Method 2: Via Oversight Hub (if configured)

Go to http://localhost:3001 and use the dashboard to:

1. Create new task
2. Monitor execution
3. View published posts
4. Check quality scores

---

## ✅ VERIFICATION CHECKLIST

Run through this after starting everything:

- [ ] **Backend Health:**

  ```bash
  curl http://localhost:8000/api/health
  # Should return: {"status": "healthy"}
  ```

- [ ] **Strapi Connection:**

  ```bash
  curl http://localhost:1337/admin
  # Should return: Strapi admin page
  ```

- [ ] **Orchestrator Status:**

  ```bash
  curl http://localhost:8000/api/agents/status
  # Should show agent configurations
  ```

- [ ] **Task Pipeline Ready:**
  ```bash
  curl http://localhost:8000/api/tasks
  # Should return: []  (empty task list or existing tasks)
  ```

---

## 📊 TESTING THE PRODUCTION PIPELINE

### Quick Test (5 minutes)

**Terminal Command:**

```powershell
# Create a test task
$response = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks" `
  -Method POST `
  -ContentType "application/json" `
  -Body @'
{
  "task_name": "Test Blog Post",
  "topic": "The Future of Artificial Intelligence",
  "primary_keyword": "AI technology trends",
  "target_audience": "tech enthusiasts",
  "category": "AI"
}
'@

$taskId = ($response.Content | ConvertFrom-Json).id
Write-Host "Task created: $taskId"

# Wait 15 seconds for processing
Start-Sleep -Seconds 15

# Check status
$status = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks/$taskId" | ConvertFrom-Json
Write-Host "Status: $($status.status)"
Write-Host "Quality Score: $($status.quality_score)/100"
Write-Host "Published: $($status.publish_status)"
Write-Host "Strapi URL: $($status.strapi_url)"
```

### Expected Output

```
Task created: 550e8400-e29b-41d4-a716-446655440000
Status: completed
Quality Score: 82/100
Published: published
Strapi URL: http://localhost:1337/api/articles/the-future-of-artificial-intelligence
```

---

## 🚨 TROUBLESHOOTING

### Problem: "Strapi client not available" or posts not publishing

**Solution:**

1. **Check Strapi API Token:**

   ```bash
   # Verify STRAPI_API_TOKEN is set in .env.local
   echo $env:STRAPI_API_TOKEN
   # Should NOT be empty
   ```

2. **Test Strapi connection:**

   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:1337/api/articles
   # Should return: {"data": [...]}
   ```

3. **Restart backend:**
   ```bash
   # Ctrl+C in backend terminal, then:
   python -m uvicorn main:app --reload --port 8000
   ```

### Problem: "Content quality below threshold" - posts not approved

**Solution:** The critique loop requires quality score ≥75. If your generated content isn't meeting this:

1. **Check content quality:** The orchestrator might not be generating good content
2. **Lower threshold temporarily** (for testing) - edit `src/cofounder_agent/services/task_executor.py` line ~280:
   ```python
   if critique_result.get("quality_score", 0) >= 70:  # Changed from 75 to 70
   ```
3. **Verify orchestrator:**
   ```bash
   curl http://localhost:8000/api/agents/status
   # Should show content agent is available
   ```

### Problem: "Orchestrator failed" or LLM errors

**Solution:**

1. **Check if using Ollama:**

   ```bash
   curl http://localhost:11434/api/tags
   # Should return list of models like ["mistral", "llama3.2"]
   ```

2. **If no Ollama, check API keys:**

   ```bash
   echo $env:OPENAI_API_KEY
   echo $env:ANTHROPIC_API_KEY
   # At least ONE should be set
   ```

3. **Check backend logs:**
   - Look for "🎬 PRODUCTION PIPELINE" messages
   - Look for "❌ PHASE 1 Failed" or error details

### Problem: "Task never completes" - stuck in pending/in_progress

**Solution:**

1. **Verify task executor is running:**

   ```bash
   curl http://localhost:8000/api/tasks/stats
   # Should show: {"running": true, "total_processed": >0}
   ```

2. **Check logs for errors:**
   - Look in backend terminal for ERROR level messages
   - Check for database connection issues

3. **Restart task executor:**
   - Restart the backend service (Ctrl+C, then restart)

---

## 📈 PRODUCTION OPERATIONS

### Monitoring

Check executor stats regularly:

```bash
curl http://localhost:8000/api/tasks/stats
```

**Expected response:**

```json
{
  "running": true,
  "total_processed": 5,
  "successful": 5,
  "failed": 0,
  "published_to_strapi": 5,
  "critique_stats": {
    "total_critiques": 5,
    "approved": 5,
    "rejected": 0,
    "approval_rate": "100.0%"
  }
}
```

### Common Production Scenarios

**Scenario 1: Generate multiple blog posts**

```powershell
$topics = @(
  "Machine Learning Best Practices",
  "Cloud Architecture Patterns",
  "DevOps Automation",
  "Data Science Tools"
)

foreach ($topic in $topics) {
  $body = @{
    task_name = "Batch Blog Generation"
    topic = $topic
    primary_keyword = $topic.Split()[0]
    target_audience = "developers"
    category = "Technology"
  } | ConvertTo-Json

  Invoke-WebRequest -Uri "http://localhost:8000/api/tasks" `
    -Method POST `
    -ContentType "application/json" `
    -Body $body

  Write-Host "Created task for: $topic"
  Start-Sleep -Seconds 2
}
```

**Scenario 2: Monitor all recent tasks**

```powershell
# Get all tasks
$allTasks = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks?limit=50" | ConvertFrom-Json

# Show summary
$allTasks | ForEach-Object {
  Write-Host "$($_.topic) - Status: $($_.status), Score: $($_.quality_score)/100, Published: $($_.publish_status)"
}
```

---

## 🚀 DEPLOYING TO PRODUCTION CLOUD

### To Cloud (Railway/Vercel)

1. **Set environment variables in cloud platform:**
   - Railway: Settings → Environment Variables
   - Vercel: Settings → Environment Variables
   - Copy all values from `.env.local` except local URLs

2. **Update URLs for production:**

   ```bash
   STRAPI_URL=https://cms.your-domain.com
   API_BASE_URL=https://api.your-domain.com
   DATABASE_URL=postgresql://prod-user:prod-pass@prod-host/glad_labs
   ```

3. **Deploy:**
   - Backend: `git push origin main` (Railway auto-deploys)
   - Frontend: `git push origin main` (Vercel auto-deploys)

---

## 📚 ADDITIONAL RESOURCES

- **[Content Critique Loop](../src/cofounder_agent/services/content_critique_loop.py)** - Quality validation logic
- **[Production Task Executor](../src/cofounder_agent/services/task_executor.py)** - Full pipeline implementation
- **[Strapi Publisher](../src/cofounder_agent/services/strapi_publisher.py)** - Strapi posting logic
- **[Architecture Docs](../../docs/02-ARCHITECTURE_AND_DESIGN.md)** - System design details
- **[AI Agents Guide](../../docs/05-AI_AGENTS_AND_INTEGRATION.md)** - Agent pipeline details

---

## 🎉 YOU'RE READY!

Your production blog generation pipeline is ready to launch. Start creating tasks via the API and watch blog posts automatically appear in Strapi CMS!

**Next Steps:**

1. ✅ Set up `.env.local` with Strapi token
2. ✅ Verify all services running
3. ✅ Create first test task
4. ✅ Confirm blog post appears in Strapi
5. ✅ Deploy to production cloud

**Questions?** Check the troubleshooting section above or review the source code in `src/cofounder_agent/services/`.

---

**Happy Blog Generation! 🚀📝**
