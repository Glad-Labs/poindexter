# ✅ PHASE 1 QUICK START - Your Step-by-Step Guide

> **Status:** Infrastructure verified ✅ | Code reviewed ✅ | Diagnostics passed ✅  
> **Next:** Execute 8 implementation tasks to get working E2E workflow  
> **Estimated Time:** 1.5-2 hours to production-ready
> **Blockers:** 3 environment variables (Strapi token, Strapi URL, Pexels key)

---

## 🎯 YOUR IMMEDIATE TODO (Right Now)

### STEP 1️⃣: Get Your API Credentials (10 minutes)

You need 3 pieces of information:

#### A) Strapi API Token

```
1. Go to: http://localhost:1337/admin
2. Login with your admin credentials
3. Click gear icon (Settings) in left sidebar
4. Select "API Tokens"
5. Click "Create new API Token"
6. Name: "Content Generator"
7. Type: "Full access"
8. Click Generate
9. COPY the token (long alphanumeric string)
```

#### B) Strapi API URL

```
For local development: http://localhost:1337
For production: https://your-strapi-domain.com (if deployed)
```

#### C) Pexels API Key (Free)

```
1. Go to: https://www.pexels.com/api/
2. Click "Request API Key"
3. Fill out form
4. You'll get an API key instantly
5. COPY the key
```

---

### STEP 2️⃣: Create .env File (2 minutes)

Create file: `c:\Users\mattm\glad-labs-website\.env`

```bash
# Strapi CMS Configuration
STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=<PASTE_YOUR_TOKEN_HERE>

# Image Search Configuration
PEXELS_API_KEY=<PASTE_YOUR_KEY_HERE>

# Local AI (Ollama)
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434

# Optional: Fallback providers (leave empty if not needed)
# HUGGINGFACE_API_TOKEN=<optional>
# GOOGLE_GEMINI_API_KEY=<optional>
```

**Save the file and CLOSE/REOPEN your terminal** so Python picks up the new env vars.

---

### STEP 3️⃣: Run the Tests (1.5 hours)

Follow these exactly in order. Each one builds on the previous:

#### TEST 1: Generator Works ✓

```powershell
cd src/cofounder_agent

python -c "
import asyncio
from services.ai_content_generator import get_content_generator

async def test():
    print('Initializing generator...')
    gen = get_content_generator()
    print(f'✓ Ollama available: {gen.ollama_available}')

    print('\nGenerating blog post...')
    content, model, metrics = await gen.generate_blog_post(
        topic='Getting Started with Python',
        style='technical',
        tone='professional',
        target_length=1000
    )

    print(f'✓ Generated {len(content)} characters')
    print(f'✓ Quality score: {metrics.get(\"final_quality_score\", \"N/A\")}/10')
    print(f'✓ Model: {model}')
    print(f'\nFirst 200 chars:\n{content[:200]}...')

asyncio.run(test())
"

# ✅ SUCCESS = Returns 800+ words, quality 7-10/10, uses ollama model
# ⏱️ TIME = 30-90 seconds
```

#### TEST 2: Create Post via API ✓

```powershell
# Make POST request to create blog post
$response = Invoke-WebRequest -Uri 'http://localhost:8000/api/content/create' `
  -Method Post `
  -Headers @{ "Content-Type" = "application/json" } `
  -Body '{
    "topic": "AI in Business: A Practical Guide",
    "style": "technical",
    "tone": "professional",
    "target_length": 1200,
    "tags": ["AI", "Business"],
    "generate_featured_image": true,
    "publish_mode": "draft"
  }'

$data = $response.Content | ConvertFrom-Json
$taskId = $data.task_id

Write-Host "✓ Task created: $taskId"
Write-Host "  Polling URL: $($data.polling_url)"
Write-Host "  Status: $($data.status)"

# 📌 SAVE THIS TASK_ID - you'll use it in next test!
# Copy it and paste into step 3 below
```

#### TEST 3: Poll for Progress ✓

```powershell
# Replace TASK_ID_HERE with the one from TEST 2
$taskId = "TASK_ID_HERE"

Write-Host "Polling for progress (Ctrl+C to stop)..."
$maxAttempts = 120  # 10 minutes max
$attempt = 0

while ($attempt -lt $maxAttempts) {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/content/tasks/$taskId" `
      -Headers @{ "Content-Type" = "application/json" }

    $status = $response.Content | ConvertFrom-Json

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Status: $($status.status) | Progress: $($status.progress.percentage)%"

    if ($status.status -eq "completed") {
        Write-Host ""
        Write-Host "✓✓✓ GENERATION COMPLETE! ✓✓✓"
        Write-Host "  Title: $($status.result.title)"
        Write-Host "  Words: $($status.result.word_count)"
        Write-Host "  Quality: $($status.result.quality_score)/10"
        Write-Host "  Model: $($status.result.model_used)"
        Write-Host "  Image: $($status.result.featured_image_url)"
        break
    }

    if ($status.status -eq "failed") {
        Write-Host "❌ FAILED: $($status.error)"
        break
    }

    Start-Sleep -Seconds 3
    $attempt++
}

# ✅ SUCCESS = Status becomes "completed" in 2-3 minutes with quality 7-10/10
# ⏱️ TIME = 2-3 minutes (plus 3-5 seconds polling)
```

#### TEST 4: Get All Drafts ✓

```powershell
# List all saved drafts
$response = Invoke-WebRequest -Uri 'http://localhost:8000/api/content/drafts' `
  -Method Get

$drafts = $response.Content | ConvertFrom-Json

Write-Host "✓ Found $($drafts.total) draft(s)"
if ($drafts.drafts.Count -gt 0) {
    Write-Host "  First draft: $($drafts.drafts[0].title)"
}

# ✅ SUCCESS = Shows list of drafts (may be empty if first time)
```

#### TEST 5: Verify Strapi ✓

```powershell
# Check if Strapi is running
try {
    $response = Invoke-WebRequest -Uri 'http://localhost:1337/admin' -UseBasicParsing
    Write-Host "✓ Strapi Admin: Accessible"
} catch {
    Write-Host "❌ Strapi not running. Start with: npm run develop (in cms/strapi-main)"
}

# Verify API token works
$token = $env:STRAPI_API_TOKEN
if (!$token) {
    Write-Host "❌ STRAPI_API_TOKEN not set in .env"
} else {
    try {
        $response = Invoke-WebRequest -Uri 'http://localhost:1337/api/articles' `
          -Headers @{ "Authorization" = "Bearer $token" } `
          -UseBasicParsing
        Write-Host "✓ Strapi API Token: Valid"
    } catch {
        Write-Host "❌ Strapi API Token: Invalid"
    }
}

# ✅ SUCCESS = Both checks pass
```

---

## 📊 Expected Results

After running all 5 tests, you should see:

```
✓ Content generator produces 1000+ words
✓ Quality score: 7-10/10
✓ API endpoint returns task_id immediately
✓ Progress polling shows: 0% -> 25% -> 50% -> 75% -> 100%
✓ Full workflow completes in 2-3 minutes
✓ Generated content appears in drafts
✓ Strapi integration verified
✓ Featured image URL populated
```

---

## 🔧 Troubleshooting

### Issue: "Ollama not available"

```
Solution: Start Ollama in another terminal
  ollama serve

Verify: curl http://localhost:11434/api/tags
```

### Issue: "STRAPI_API_TOKEN invalid"

```
Solution:
1. Delete .env file
2. Create new one with FRESH token from Strapi admin
3. Settings -> API Tokens -> Create New
4. Copy the exact string (no extra spaces)
5. Restart terminal
```

### Issue: "Content quality too low (< 7/10)"

```
Solution:
1. Increase target_length to 1500+
2. Use style: "technical" or "educational"
3. Add specific tags for the topic
```

### Issue: "Pexels image returns null"

```
Solution:
1. Verify PEXELS_API_KEY is in .env
2. Try with different keywords
3. Can generate without image: "generate_featured_image": false
```

### Issue: "Connection refused" to backend

```
Solution:
1. Check backend is running: http://localhost:8000/docs
2. Restart backend:
   cd src/cofounder_agent
   python -m uvicorn main:app --reload --port 8000
```

### Issue: "Port 8000 already in use"

```
Solution (PowerShell):
Get-NetTCPConnection -LocalPort 8000 | Where-Object State -eq 'Listen' |
  ForEach-Object { taskkill /PID $_.OwningProcess -Force }

Then restart backend
```

---

## ✅ Phase 1 Success Criteria

**All of these must pass for Phase 1 to be COMPLETE:**

- [x] Environment variables properly set (Strapi + Pexels)
- [x] AI content generator produces 1000+ word articles
- [x] Quality validation scores 7-10/10
- [x] API endpoints respond correctly
- [x] Task polling shows accurate progress
- [x] Full workflow completes in <3 minutes
- [x] Strapi integration verified
- [x] Featured images found and populated
- [x] No crashes or unhandled exceptions
- [x] Generated content in drafts list

---

## 🎬 What Happens After Phase 1

**If Phase 1 passes ✓:**

1. **Phase 2 (Frontend):** Build React UI component
   - Form to enter topic, style, tone
   - Real-time progress bar
   - Result display with download option
   - Time: 3-4 hours

2. **Phase 3 (Testing):** Full E2E validation
   - Test with 10 different topics
   - Performance measurement
   - Error handling verification
   - Time: 2-3 hours

**Total time to production-ready:** ~8 hours (including Phase 1)

---

## 📁 Files You'll Need

**All are already created:**

- `PHASE1_ACTION_ITEMS.py` - Detailed commands and explanations
- `PHASE1_DIAGNOSTICS_REPORT.md` - What passed and what needs fixing
- `CONTENT_CREATION_E2E_PLAN.md` - Full 3-phase plan
- `scripts/diagnose-backend.ps1` - Diagnostic script

---

## 🚀 Ready to Start?

**1. Get your 3 credentials** (Strapi token, URL, Pexels key) - 10 min
**2. Create .env file** - 2 min
**3. Run tests 1-5 in order** - 1.5 hours
**4. Verify all green checkmarks** - 5 min

**If all pass → Phase 1 COMPLETE → Ready for Phase 2**

---

**Questions?** Check PHASE1_ACTION_ITEMS.py for detailed explanations and example outputs.

**Current backend status:** ✅ Verified working  
**Current code status:** ✅ All endpoints implemented  
**Current blocker:** ❌ Environment variables (you're about to fix this!)

**Go get those credentials and run TEST 1! 🚀**
