# 🔧 API to Strapi Pipeline - Troubleshooting Guide

**Last Updated:** November 6, 2025

---

## Quick Diagnostics

### ❌ Backend not responding

**Symptom:** `Cannot connect to http://localhost:8000`

**Check:**

```powershell
# 1. Is backend running?
curl -Uri "http://localhost:8000/api/health"

# 2. Are there errors in backend terminal?
# Look in Terminal 2 for Python errors

# 3. Is port 8000 being used by something else?
netstat -ano | findstr :8000
```

**Fix:**

```powershell
# Terminal 2: Start backend
cd src\cofounder_agent
python -m uvicorn main:app --reload
```

---

### ❌ Strapi connection failed

**Symptom:** `Cannot connect to http://localhost:1337` OR `401 Unauthorized`

**Check:**

```powershell
# 1. Is Strapi running?
curl -Uri "http://localhost:1337/admin"

# 2. Is token valid?
echo $env:STRAPI_API_TOKEN

# 3. Try to fetch articles
$headers = @{ "Authorization" = "Bearer YOUR_TOKEN" }
curl -Uri "http://localhost:1337/api/articles" -Headers $headers
```

**Fix - Strapi not running:**

```powershell
# Terminal 1: Start Strapi
cd cms\strapi-v5-backend
npm run develop
```

**Fix - Invalid token:**

1. Go to http://localhost:1337/admin
2. Settings → API Tokens
3. Create new token or copy existing one
4. Add to .env.local: `STRAPI_API_TOKEN=token-here`

---

### ⏳ Task stuck in "pending"

**Symptom:** Task created but status never changes from `pending`

**Check:**

```powershell
# 1. Is task executor running in backend?
# Look in Terminal 2 for: "TaskExecutor started" or similar

# 2. Check backend logs for errors
# Scroll Terminal 2 to see any error messages

# 3. Is database connected?
# Look for "Database connection established" message
```

**Fix:**

```powershell
# Restart backend
# 1. Ctrl+C in Terminal 2
# 2. Rerun: python -m uvicorn main:app --reload

# Check for database errors in logs
```

---

### ⚠️ Content quality score too low

**Symptom:** Quality score < 75, content not published

**Check:**

```powershell
# 1. Is LLM/orchestrator available?
curl -Uri "http://localhost:8000/api/agents/status"

# 2. What's the generated content?
# Look in task result for: generated_content or content

# 3. Check critique feedback
# Look for: critique_feedback and critique_suggestions
```

**Fix - Orchestrator not available:**

```powershell
# Verify LLM is running
# Option 1: Ollama
ollama serve

# Option 2: Check OpenAI key
echo $env:OPENAI_API_KEY
```

**Fix - Improve content quality:**

- Check that generated content is complete (not truncated)
- Verify orchestrator is responding correctly
- Check logs for LLM errors

---

### ❌ Post not appearing in Strapi

**Symptom:** `publish_status = "published"` but post not in Strapi

**Check:**

```powershell
# 1. Is the post ID valid?
# Look for: strapi_post_id in task result

# 2. Verify in Strapi API
$headers = @{ "Authorization" = "Bearer YOUR_TOKEN" }
curl -Uri "http://localhost:1337/api/articles/POST_ID" -Headers $headers

# 3. Check all recent articles
curl -Uri "http://localhost:1337/api/articles?sort=-createdAt&pagination[limit]=5" `
     -Headers $headers
```

**Fix:**

```powershell
# 1. Verify token again
# Settings → API Tokens → Copy exact token

# 2. Check publish endpoint is working
$post = @{ data = @{ title = "Test"; content = "test" } } | ConvertTo-Json
curl -Uri "http://localhost:1337/api/articles" `
     -Method Post `
     -Body $post `
     -ContentType "application/json" `
     -Headers $headers
```

---

### 🐢 Pipeline very slow

**Symptom:** Tasks taking >60 seconds to complete

**Possible causes:**

1. **Ollama model loading**
   - First request loads model from disk (~30-60 seconds)
   - Subsequent requests faster
   - **Fix:** Run `ollama pull mistral` to pre-load model

2. **Network latency**
   - If using cloud LLM (OpenAI, Claude, Google)
   - **Fix:** Check internet connection, API quota

3. **Database queries**
   - PostgreSQL slow
   - **Fix:** Check database logs, verify connection

4. **Orchestrator processing**
   - Multi-agent pipeline takes time
   - **Fix:** Review orchestrator logs for bottlenecks

**Check execution time:**

```powershell
# Create task and time it
$start = Get-Date
$task = Invoke-RestMethod -Uri "http://localhost:8000/api/tasks" `
    -Method Post -Body @{...}

# Monitor
while ($true) {
    $result = Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/$($task.id)"
    if ($result.status -eq "completed") {
        $elapsed = ((Get-Date) - $start).TotalSeconds
        Write-Host "Completed in $elapsed seconds"
        break
    }
    Start-Sleep -Seconds 2
}
```

---

### 📄 Content generation using fallback template

**Symptom:** Generated content looks generic/templated

**Why:** Orchestrator unavailable, using fallback template

**Check:**

```powershell
# 1. Is orchestrator running?
curl -Uri "http://localhost:8000/api/agents/status"

# 2. Check backend logs for orchestrator errors
# Look in Terminal 2 for: "orchestrator" or "agent" errors

# 3. Verify LLM connection
curl -Uri "http://localhost:8000/api/models/status"
```

**Fix:**

- Verify orchestrator is initialized
- Check LLM provider is accessible
- Review backend logs for specific errors

---

## Common Error Messages

### "STRAPI_API_TOKEN not provided"

**Solution:**

```bash
# Add to .env.local:
STRAPI_API_TOKEN=your-token-here
```

### "Cannot connect to orchestrator"

**Solution:**

- Verify orchestrator initialized in backend
- Check backend logs
- Ensure agents module is available

### "Content rejected by critique loop"

**Solution:**

- Quality score < 75
- Review critique_feedback in task result
- May retry automatically if retry logic enabled

### "TimeoutError connecting to Strapi"

**Solution:**

- Strapi taking too long to respond
- Verify Strapi not under heavy load
- Check `STRAPI_URL` in `.env.local`

---

## Verification Checklist

Before declaring pipeline working:

- [ ] Backend responds to health check
- [ ] Strapi accessible with valid token
- [ ] Task created via API successfully
- [ ] Task status changes from pending → in_progress
- [ ] Generated content > 300 characters
- [ ] Quality score ≥ 75
- [ ] Blog post appears in Strapi
- [ ] Post marked as "published"
- [ ] Post accessible via Strapi API
- [ ] Stats show increased published_count

---

## Performance Baselines

**Healthy pipeline timings:**

| Phase         | Typical Time  | Max Time       |
| ------------- | ------------- | -------------- |
| Task creation | <1 second     | 2 seconds      |
| Generation    | 10-30 sec     | 60 seconds     |
| Critique      | 2-5 seconds   | 15 seconds     |
| Publishing    | 1-3 seconds   | 10 seconds     |
| **Total**     | **15-40 sec** | **90 seconds** |

If consistently > 60 seconds:

- Check Ollama/LLM performance
- Review database query times
- Monitor network latency

---

## Getting Detailed Logs

### Backend Logs

```powershell
# Terminal 2 where backend is running
# Look for:
# - Task executor polling
# - Orchestrator calls
# - Critique validation results
# - Strapi publishing status

# To increase verbosity:
# Restart with: python -m uvicorn main:app --reload --log-level debug
```

### Strapi Logs

```powershell
# Terminal 1 where Strapi running
# Look for:
# - API token validation
# - POST /api/articles requests
# - Database write operations
```

### Ollama Logs

```powershell
# Terminal 3 if using Ollama
# Look for:
# - Model loading
# - Token generation
# - Response times
```

---

## Reset Everything

If all else fails:

```powershell
# 1. Stop all services (Ctrl+C in each terminal)

# 2. Clear caches
rm -r .cache
rm -r node_modules

# 3. Reinstall
npm install --workspaces

# 4. Reset database (WARNING: deletes all data)
# Option A: SQLite
rm .tmp/data.db

# Option B: PostgreSQL (if using)
# In PostgreSQL client:
# DROP DATABASE glad_labs;
# CREATE DATABASE glad_labs;

# 5. Restart services
# Terminal 1: Strapi
cd cms\strapi-v5-backend
npm run develop

# Terminal 2: Backend
cd src\cofounder_agent
python -m uvicorn main:app --reload

# Terminal 3: Ollama (if using)
ollama serve

# 6. Run test again
.\test_api_to_strapi.ps1
```

---

## Still Stuck?

1. **Review logs in all terminals**
   - Backend terminal (Python errors)
   - Strapi terminal (Node errors)
   - Ollama terminal (Model errors)

2. **Check database connectivity**
   - Verify `DATABASE_URL` in `.env.local`
   - Test connection: `psql $DATABASE_URL -c "SELECT 1"`

3. **Verify all environment variables**

   ```powershell
   # Should all be set:
   $env:STRAPI_URL
   $env:STRAPI_API_TOKEN
   $env:API_BASE_URL
   $env:USE_OLLAMA
   $env:DATABASE_URL
   ```

4. **Review PRODUCTION_LAUNCH_GUIDE.md**
   - More detailed setup instructions
   - Additional configuration options

5. **Check task result directly**
   ```powershell
   # See full error details
   Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/TASK_ID" |
       Select-Object error, publish_error, orchestrator_error |
       Format-List
   ```

---

**Need help? Check the detailed test guide: `API_TO_STRAPI_TEST_GUIDE.md`**
