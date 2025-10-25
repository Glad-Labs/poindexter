# 🚀 Quick Test: End-to-End Workflow (5-10 Minutes)

**Goal:** Verify the complete workflow works end-to-end in less than 10 minutes

**Prerequisites:**

- Ollama running on localhost:11434 with a model loaded
- Strapi running on localhost:1337 with API token
- Backend running on localhost:8000
- PowerShell terminal open

---

## Step 1: Verify Services Are Running (2 minutes)

### 1.1 Check Ollama

```powershell
curl http://localhost:11434/api/tags
```

**Expected Response:**

```json
{"models":[{"name":"mistral:latest",...}]}
```

### 1.2 Check Strapi is Responding

```powershell
curl http://localhost:1337/admin
```

**Expected Response:** HTML page loads

### 1.3 Check Backend API

```powershell
curl http://localhost:8000/docs
```

**Expected Response:** Swagger UI documentation loads

---

## Step 2: Set Environment Variables (1 minute)

```powershell
# Get your Strapi token first from http://localhost:1337/admin
# Settings > API Tokens > Create

# Set in PowerShell:
$env:STRAPI_URL = "http://localhost:1337"
$env:STRAPI_API_TOKEN = "your-token-here"  # Replace with actual token
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "mistral"

# Verify they're set:
echo "Strapi URL: $env:STRAPI_URL"
echo "Ollama Host: $env:OLLAMA_HOST"
echo "Ollama Model: $env:OLLAMA_MODEL"
```

---

## Step 3: Test Content Generation (3-5 minutes)

### 3.1 Trigger Blog Post Generation

```powershell
# Make the request (save as test-generate.json first)
$body = @{
    topic = "The Future of AI in Business"
    style = "technical"
    tone = "professional"
    target_length = 1000
    tags = @("ai", "business", "technology")
} | ConvertTo-Json

curl -X POST http://localhost:8000/api/content/generate `
  -H "Content-Type: application/json" `
  -d $body
```

**Expected Response:**

```json
{
  "task_id": "12345-abcde-67890",
  "status": "pending",
  "message": "Post generation started. Check /api/content/status/12345-abcde-67890 for progress."
}
```

**Save the task_id** (you'll need it next)

### 3.2 Check Generation Status

Wait 30-60 seconds for Ollama to generate, then:

```powershell
# Replace TASK_ID with your task ID from above
$TASK_ID = "12345-abcde-67890"
curl http://localhost:8000/api/content/status/$TASK_ID
```

**Expected Response (while generating):**

```json
{
  "task_id": "12345-abcde-67890",
  "status": "processing",
  "created_at": "2025-10-25T14:30:00Z",
  "result": null
}
```

**Expected Response (after complete):**

```json
{
  "task_id": "12345-abcde-67890",
  "status": "completed",
  "created_at": "2025-10-25T14:30:00Z",
  "result": {
    "title": "The Future of AI in Business",
    "slug": "the-future-of-ai-in-business",
    "content": "# The Future of AI in Business\n\n...",
    "topic": "The Future of AI in Business",
    "tags": ["ai", "business", "technology"],
    "generated_at": "2025-10-25T14:31:45Z"
  },
  "error": null
}
```

---

## Step 4: Save to Strapi (1 minute)

Once generation is complete:

```powershell
$TASK_ID = "12345-abcde-67890"  # Use your task ID

$saveBody = @{
    task_id = $TASK_ID
    publish = $true  # Auto-publish
} | ConvertTo-Json

curl -X POST http://localhost:8000/api/content/save-to-strapi `
  -H "Content-Type: application/json" `
  -d $saveBody
```

**Expected Response:**

```json
{
  "strapi_post_id": 42,
  "title": "The Future of AI in Business",
  "slug": "the-future-of-ai-in-business",
  "status": "published",
  "message": "Post saved to Strapi as published. Post ID: 42"
}
```

---

## Step 5: Verify Post in Strapi (1 minute)

Visit http://localhost:1337/admin and check:

1. Click "Content Manager" → "Posts"
2. Your generated post should appear
3. Status should be "Published" ✅

---

## Step 6: Verify Post on Public Site (1 minute)

Visit http://localhost:3000

Your generated post should appear on the homepage! ✅

---

## ✅ Success Criteria

- [ ] Ollama generates content without errors
- [ ] Backend receives task and returns task_id
- [ ] Status changes from "pending" → "processing" → "completed"
- [ ] Generated content is valid markdown with title
- [ ] Post saves to Strapi successfully
- [ ] Post appears in Strapi admin
- [ ] Post appears on Public Site homepage

---

## 🐛 Troubleshooting

### Issue: Ollama Connection Refused

```
curl: (7) Failed to connect to localhost port 11434: Connection refused
```

**Fix:**

```powershell
ollama serve
```

---

### Issue: Strapi API Returns 401

```
"Strapi API error: 401"
```

**Fix:**

- Check your API token is correct
- Generate a new token in http://localhost:1337/admin → Settings → API Tokens
- Verify `$env:STRAPI_API_TOKEN` is set

---

### Issue: "Task not found" Error

```
"Task not found. Check task_id."
```

**Fix:**

- Ensure you're using the correct task_id from the generate response
- Don't wait more than 30 minutes (tasks are cleared on restart)

---

### Issue: Generation Times Out

```
"Task status: error - Connection timeout"
```

**Fix:**

- Ollama needs 1-3 minutes on first run
- Try a smaller model: `ollama pull phi` (faster)
- Or wait longer and check status again

---

### Issue: Content is Empty

```
"result": null or "result": {"content": ""}
```

**Fix:**

- Check Ollama model is actually generating
- Try different topic or style
- Verify Ollama is responding: `curl http://localhost:11434/api/tags`

---

## 📝 Full Workflow Script (Copy & Paste)

Save as `test-e2e.ps1` and run with `.\test-e2e.ps1`:

```powershell
# ============================================================================
# GLAD Labs E2E Workflow Test
# ============================================================================

# Set environment
$env:STRAPI_URL = "http://localhost:1337"
$env:STRAPI_API_TOKEN = "your-token-here"  # UPDATE THIS
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "mistral"

Write-Host "🚀 GLAD Labs E2E Workflow Test" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan

# 1. Verify services
Write-Host "`n1️⃣ Verifying services..." -ForegroundColor Yellow
$ollamaOk = (curl -s http://localhost:11434/api/tags).Length -gt 0
$strapiOk = (curl -s http://localhost:1337/admin).Length -gt 0
$backendOk = (curl -s http://localhost:8000/docs).Length -gt 0

Write-Host "   Ollama: $(if($ollamaOk) {'✅'} else {'❌'})"
Write-Host "   Strapi: $(if($strapiOk) {'✅'} else {'❌'})"
Write-Host "   Backend: $(if($backendOk) {'✅'} else {'❌'})"

if (-not ($ollamaOk -and $strapiOk -and $backendOk)) {
    Write-Host "`n❌ Not all services are running!" -ForegroundColor Red
    exit 1
}

# 2. Generate content
Write-Host "`n2️⃣ Generating blog post with Ollama..." -ForegroundColor Yellow
$generateBody = @{
    topic = "How to Optimize AI Costs"
    style = "technical"
    tone = "professional"
    target_length = 1000
    tags = @("ai", "cost", "optimization")
} | ConvertTo-Json

$generateResponse = curl -s -X POST http://localhost:8000/api/content/generate `
  -H "Content-Type: application/json" `
  -d $generateBody | ConvertFrom-Json

$taskId = $generateResponse.task_id
Write-Host "   Task ID: $taskId"
Write-Host "   Status: $($generateResponse.status)"
Write-Host "   Waiting for generation (this takes 1-3 minutes)..." -ForegroundColor Cyan

# 3. Poll for completion
Write-Host "`n3️⃣ Polling for generation completion..." -ForegroundColor Yellow
$maxAttempts = 60
$attempt = 0
$completed = $false

while ($attempt -lt $maxAttempts) {
    Start-Sleep -Seconds 5
    $attempt++

    $statusResponse = curl -s http://localhost:8000/api/content/status/$taskId | ConvertFrom-Json
    $status = $statusResponse.status

    Write-Host "   Attempt $attempt/$maxAttempts - Status: $status"

    if ($status -eq "completed") {
        $completed = $true
        break
    }
    elseif ($status -eq "error") {
        Write-Host "   ❌ Generation failed: $($statusResponse.error)" -ForegroundColor Red
        exit 1
    }
}

if (-not $completed) {
    Write-Host "`n❌ Generation timeout!" -ForegroundColor Red
    exit 1
}

Write-Host "`n✅ Generation complete!" -ForegroundColor Green

# 4. Save to Strapi
Write-Host "`n4️⃣ Saving post to Strapi..." -ForegroundColor Yellow
$saveBody = @{
    task_id = $taskId
    publish = $true
} | ConvertTo-Json

$saveResponse = curl -s -X POST http://localhost:8000/api/content/save-to-strapi `
  -H "Content-Type: application/json" `
  -d $saveBody | ConvertFrom-Json

if ($null -eq $saveResponse.strapi_post_id) {
    Write-Host "   ❌ Failed to save: $(ConvertTo-Json $saveResponse)" -ForegroundColor Red
    exit 1
}

Write-Host "   Post ID: $($saveResponse.strapi_post_id)"
Write-Host "   Title: $($saveResponse.title)"
Write-Host "   Status: $($saveResponse.status)"

# 5. Success
Write-Host "`n✅ E2E Workflow Complete!" -ForegroundColor Green
Write-Host "`n📝 Next Steps:" -ForegroundColor Cyan
Write-Host "   1. Visit http://localhost:1337/admin and check Content Manager > Posts"
Write-Host "   2. Visit http://localhost:3000 and verify post appears on homepage"
Write-Host "   3. You can now generate posts on demand! 🎉"
```

---

## 🎯 Next Steps After Success

1. **Update Oversight Hub UI** - Add button to trigger generation from dashboard
2. **Add Featured Image Generation** - Use DALL-E or Stable Diffusion
3. **Add Schedule Publishing** - Schedule posts for future dates
4. **Add Multi-language** - Generate content in different languages
5. **Add Performance Metrics** - Track generation time and quality

---

## 📚 API Reference

### Generate Blog Post

```
POST /api/content/generate
Content-Type: application/json

{
    "topic": "string",              # Required
    "style": "technical|narrative|listicle",  # Optional, default: "technical"
    "tone": "professional|casual|academic",   # Optional, default: "professional"
    "target_length": 1000,          # Optional, default: 1500 (range: 300-5000)
    "tags": ["tag1", "tag2"]        # Optional
}
```

### Check Status

```
GET /api/content/status/{task_id}
```

### Save to Strapi

```
POST /api/content/save-to-strapi
Content-Type: application/json

{
    "task_id": "uuid",
    "publish": false  # Optional, default: false (draft)
}
```

---

**Estimated Time:** 5-10 minutes  
**Difficulty:** Easy  
**Status:** ✅ Ready to test
