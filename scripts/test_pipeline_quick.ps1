#!/usr/bin/env powershell
<#
.SYNOPSIS
    Quick Start - Test API to Strapi Pipeline
    
.DESCRIPTION
    This script guides you through testing your blog generation pipeline
    from REST API creation to Strapi publication
#>

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     QUICK START - API TO STRAPI PIPELINE TEST                 ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
Write-Host "📋 CHECKING PREREQUISITES..." -ForegroundColor Yellow
Write-Host ""

$missingItems = @()

# Check if .env.local exists
if (-not (Test-Path ".env.local")) {
    $missingItems += ".env.local file not found"
}
else {
    Write-Host "✅ .env.local exists" -ForegroundColor Green
}

# Check if STRAPI_API_TOKEN is set
$strapiToken = [Environment]::GetEnvironmentVariable("STRAPI_API_TOKEN")
if (-not $strapiToken) {
    # Try to read from .env.local
    $envContent = Get-Content ".env.local" -ErrorAction SilentlyContinue
    $strapiToken = ($envContent | Select-String "STRAPI_API_TOKEN" | ForEach-Object { $_.ToString().Split('=')[1].Trim() })
}

if ($strapiToken) {
    Write-Host "✅ STRAPI_API_TOKEN is set" -ForegroundColor Green
}
else {
    $missingItems += "STRAPI_API_TOKEN not found in environment or .env.local"
}

# Check if backend is running
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
    Write-Host "✅ Backend API is running (http://localhost:8000)" -ForegroundColor Green
}
catch {
    $missingItems += "Backend API not responding (http://localhost:8000)"
}

# Check if Strapi is running
try {
    $strapiCheck = Invoke-WebRequest -Uri "http://localhost:1337/admin" -TimeoutSec 2 -ErrorAction SilentlyContinue
    Write-Host "✅ Strapi CMS is running (http://localhost:1337)" -ForegroundColor Green
}
catch {
    $missingItems += "Strapi CMS not responding (http://localhost:1337)"
}

Write-Host ""

# Show missing items if any
if ($missingItems.Count -gt 0) {
    Write-Host "❌ MISSING PREREQUISITES:" -ForegroundColor Red
    Write-Host ""
    $missingItems | ForEach-Object {
        Write-Host "  • $_" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "📝 SETUP STEPS:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1️⃣  Create .env.local in project root with:" -ForegroundColor White
    Write-Host ""
    Write-Host "    STRAPI_URL=http://localhost:1337" -ForegroundColor Cyan
    Write-Host "    STRAPI_API_TOKEN=your-token-here" -ForegroundColor Cyan
    Write-Host "    USE_OLLAMA=true" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "2️⃣  Get Strapi API Token:" -ForegroundColor White
    Write-Host "    • Start Strapi: cd cms\strapi-main; npm run develop" -ForegroundColor Cyan
    Write-Host "    • Go to: http://localhost:1337/admin" -ForegroundColor Cyan
    Write-Host "    • Settings → API Tokens → Create → Copy token" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "3️⃣  Start Services (each in separate terminal):" -ForegroundColor White
    Write-Host "    • Strapi: cd cms\strapi-main; npm run develop" -ForegroundColor Cyan
    Write-Host "    • Backend: cd src\cofounder_agent; python -m uvicorn main:app --reload" -ForegroundColor Cyan
    Write-Host "    • Ollama: ollama serve (if using local AI)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "4️⃣  Run this script again when ready" -ForegroundColor White
    Write-Host ""
    exit 1
}

Write-Host "✅ ALL PREREQUISITES MET - Ready to test!" -ForegroundColor Green
Write-Host ""

# Prompt to run full test
Write-Host "🚀 RUNNING FULL PIPELINE TEST..." -ForegroundColor Yellow
Write-Host ""
Write-Host "This will:" -ForegroundColor White
Write-Host "  1. Create a blog task via REST API"
Write-Host "  2. Monitor task execution (generation + critique + publishing)"
Write-Host "  3. Verify blog post in Strapi database"
Write-Host "  4. Show you the complete results"
Write-Host ""

# Load environment variables from .env.local
$envContent = Get-Content ".env.local" -ErrorAction SilentlyContinue
if ($envContent) {
    $envContent | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($name, $value)
        }
    }
}

# Configuration from environment
$BACKEND_URL = [Environment]::GetEnvironmentVariable("API_BASE_URL") ?? "http://localhost:8000"
$STRAPI_URL = [Environment]::GetEnvironmentVariable("STRAPI_URL") ?? "http://localhost:1337"
$STRAPI_TOKEN = [Environment]::GetEnvironmentVariable("STRAPI_API_TOKEN")

Write-Host "Configuration:" -ForegroundColor White
Write-Host "  Backend: $BACKEND_URL" -ForegroundColor Gray
Write-Host "  Strapi:  $STRAPI_URL" -ForegroundColor Gray
Write-Host "  Token:   $(if ($STRAPI_TOKEN) { '✅ Set' } else { '❌ Not set' })" -ForegroundColor Gray
Write-Host ""

# Create task
Write-Host "📝 Creating test task..." -ForegroundColor Yellow

$taskPayload = @{
    task_name = "E2E Pipeline Test - $(Get-Date -Format 'HH:mm:ss')"
    topic = "The Future of AI in Enterprise Business Automation"
    primary_keyword = "artificial intelligence, business automation, AI implementation"
    target_audience = "Enterprise leaders, CTO, business managers"
    category = "AI & Technology"
} | ConvertTo-Json

try {
    $taskResponse = Invoke-RestMethod `
        -Uri "$BACKEND_URL/api/tasks" `
        -Method Post `
        -Body $taskPayload `
        -ContentType "application/json" `
        -TimeoutSec 10 `
        -ErrorAction Stop
    
    $taskId = $taskResponse.id
    Write-Host "✅ Task created: $taskId" -ForegroundColor Green
}
catch {
    Write-Host "❌ Failed to create task: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "⏳ Monitoring task execution (max 90 seconds)..." -ForegroundColor Yellow
Write-Host ""

# Monitor execution
$startTime = Get-Date
$completed = $false
$lastStatus = ""

while (((Get-Date) - $startTime).TotalSeconds -lt 90) {
    try {
        $taskStatus = Invoke-RestMethod `
            -Uri "$BACKEND_URL/api/tasks/$taskId" `
            -Method Get `
            -TimeoutSec 5 `
            -ErrorAction Stop
        
        $status = $taskStatus.status
        
        if ($status -ne $lastStatus) {
            $elapsed = [int]((Get-Date) - $startTime).TotalSeconds
            Write-Host "  [$($elapsed)s] Status: $status" -ForegroundColor Cyan
            $lastStatus = $status
        }
        
        if ($status -eq "completed") {
            $completed = $true
            $finalTask = $taskStatus
            break
        }
    }
    catch {
        # Silently retry
    }
    
    Start-Sleep -Seconds 2
}

if (-not $completed) {
    Write-Host ""
    Write-Host "❌ Task did not complete in time" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "📊 RESULTS:" -ForegroundColor Yellow
Write-Host ""

# Show results
$contentLength = $finalTask.content_length ?? 0
$qualityScore = $finalTask.quality_score ?? 0
$strapiPostId = $finalTask.strapi_post_id
$publishStatus = $finalTask.publish_status

Write-Host "✅ Generation:" -ForegroundColor Green
Write-Host "   Content: $contentLength characters" -ForegroundColor Gray
Write-Host "   Quality: $qualityScore/100" -ForegroundColor Gray

if ($qualityScore -ge 75) {
    Write-Host "   Status: ✅ APPROVED" -ForegroundColor Green
}
else {
    Write-Host "   Status: ⚠️  LOW QUALITY" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "📰 Publishing:" -ForegroundColor Green
Write-Host "   Status: $publishStatus" -ForegroundColor Gray

if ($strapiPostId) {
    Write-Host "   Post ID: $strapiPostId" -ForegroundColor Gray
    
    # Verify in Strapi
    try {
        $strapiPost = Invoke-RestMethod `
            -Uri "$STRAPI_URL/api/articles/$strapiPostId" `
            -Headers @{"Authorization" = "Bearer $STRAPI_TOKEN"} `
            -TimeoutSec 5 `
            -ErrorAction Stop
        
        $postData = $strapiPost.data
        Write-Host "   Title: $($postData.title)" -ForegroundColor Gray
        Write-Host "   Slug: $($postData.slug)" -ForegroundColor Gray
        Write-Host "   Status: $($postData.status)" -ForegroundColor Gray
    }
    catch {
        Write-Host "   ⚠️  Could not verify in Strapi" -ForegroundColor Yellow
    }
}
else {
    Write-Host "   ⚠️  Not published" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║ ✅ PIPELINE TEST COMPLETE                                      ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "📝 Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Create more tasks to test consistency" -ForegroundColor White
Write-Host "  2. Check your blog: http://localhost:3000" -ForegroundColor White
Write-Host "  3. Monitor stats: curl http://localhost:8000/api/tasks/stats" -ForegroundColor White
Write-Host "  4. Ready to deploy? See PRODUCTION_LAUNCH_GUIDE.md" -ForegroundColor White
Write-Host ""

Write-Host "📚 Documentation:" -ForegroundColor Cyan
Write-Host "  • Full details: API_TO_STRAPI_TEST_GUIDE.md" -ForegroundColor Gray
Write-Host "  • Production setup: PRODUCTION_LAUNCH_GUIDE.md" -ForegroundColor Gray
Write-Host ""
