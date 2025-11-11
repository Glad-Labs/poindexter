# ============================================================================
# GLAD LABS PIPELINE TEST SCRIPT
# ============================================================================
# Tests complete flow: task creation → database → publishing → Strapi
# Run this after you've confirmed tasks exist in PostgreSQL
# ============================================================================

param(
    [string]$TaskId = $null,
    [string]$JwtToken = $null
)

Write-Host "
╔══════════════════════════════════════════════════════════════════════════╗
║           GLAD LABS POST GENERATION PIPELINE - TEST SCRIPT              ║
╚══════════════════════════════════════════════════════════════════════════╝
" -ForegroundColor Cyan

# ============================================================================
# STEP 1: CHECK IF BACKEND IS RUNNING
# ============================================================================

Write-Host "`n[STEP 1] Checking if FastAPI backend is running..." -ForegroundColor Yellow

try {
    $healthResponse = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -TimeoutSec 5
    $healthData = $healthResponse.Content | ConvertFrom-Json
    Write-Host "✅ Backend is running" -ForegroundColor Green
    Write-Host "   Status: $($healthData.status)" -ForegroundColor Green
} catch {
    Write-Host "❌ Backend is NOT running" -ForegroundColor Red
    Write-Host "   Start it with: npm run dev:cofounder" -ForegroundColor Yellow
    exit 1
}

# ============================================================================
# STEP 2: GET TASK LIST FROM DATABASE
# ============================================================================

Write-Host "`n[STEP 2] Fetching tasks from database..." -ForegroundColor Yellow

try {
    $tasksResponse = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks" -TimeoutSec 5
    $tasksData = $tasksResponse.Content | ConvertFrom-Json
    $taskCount = $tasksData.count
    
    if ($taskCount -eq 0) {
        Write-Host "❌ No tasks found in database" -ForegroundColor Red
        Write-Host "   Create a task in oversight-hub first" -ForegroundColor Yellow
        exit 1
    }
    
    Write-Host "✅ Found $taskCount tasks" -ForegroundColor Green
    
    # Get pending and completed task counts
    $pendingCount = ($tasksData.tasks | Where-Object { $_.status -eq "pending" }).Count
    $completedCount = ($tasksData.tasks | Where-Object { $_.status -eq "completed" }).Count
    
    Write-Host "   Pending: $pendingCount" -ForegroundColor Cyan
    Write-Host "   Completed: $completedCount" -ForegroundColor Cyan
    
    # If no task ID provided, use first pending task
    if (-not $TaskId) {
        $firstPending = $tasksData.tasks | Where-Object { $_.status -eq "pending" } | Select-Object -First 1
        if ($firstPending) {
            $TaskId = $firstPending.id
            Write-Host "   Using pending task: $TaskId" -ForegroundColor Gray
        } else {
            Write-Host "❌ No pending tasks found" -ForegroundColor Red
            Write-Host "   All tasks must complete first" -ForegroundColor Yellow
            exit 1
        }
    }
} catch {
    Write-Host "❌ Failed to fetch tasks: $_" -ForegroundColor Red
    exit 1
}

# ============================================================================
# STEP 3: GET SPECIFIC TASK DETAILS
# ============================================================================

Write-Host "`n[STEP 3] Getting details for task: $TaskId" -ForegroundColor Yellow

try {
    $taskResponse = Invoke-WebRequest -Uri "http://localhost:8000/api/tasks/$TaskId" -TimeoutSec 5
    $taskData = $taskResponse.Content | ConvertFrom-Json
    
    Write-Host "✅ Task found" -ForegroundColor Green
    Write-Host "   Title: $($taskData.title)" -ForegroundColor Cyan
    Write-Host "   Status: $($taskData.status)" -ForegroundColor Cyan
    Write-Host "   Topic: $($taskData.topic)" -ForegroundColor Cyan
    Write-Host "   Has result: $(if ($taskData.result) { 'Yes' } else { 'No' })" -ForegroundColor Cyan
    
    if ($taskData.status -eq "pending") {
        Write-Host "   ⚠️  Task is pending - needs to be completed first" -ForegroundColor Yellow
    }
} catch {
    Write-Host "❌ Failed to get task details: $_" -ForegroundColor Red
    exit 1
}

# ============================================================================
# STEP 4: TEST PUBLISH ENDPOINT
# ============================================================================

Write-Host "`n[STEP 4] Testing publish endpoint..." -ForegroundColor Yellow

if ($taskData.status -ne "completed") {
    Write-Host "⚠️  Task status is '$($taskData.status)', not 'completed'" -ForegroundColor Yellow
    Write-Host "   (Publishing requires status='completed')" -ForegroundColor Yellow
    Write-Host "   → Go to oversight-hub and manually approve/publish the task" -ForegroundColor Yellow
    Write-Host "   → Or query database and manually update status" -ForegroundColor Yellow
} else {
    Write-Host "Task is completed, attempting to publish..." -ForegroundColor Cyan
    
    try {
        if ($JwtToken) {
            $publishResponse = Invoke-WebRequest `
                -Uri "http://localhost:8000/api/tasks/$TaskId/publish" `
                -Method POST `
                -Headers @{ "Authorization" = "Bearer $JwtToken" } `
                -TimeoutSec 10
        } else {
            $publishResponse = Invoke-WebRequest `
                -Uri "http://localhost:8000/api/tasks/$TaskId/publish" `
                -Method POST `
                -TimeoutSec 10
        }
        
        $publishData = $publishResponse.Content | ConvertFrom-Json
        Write-Host "✅ Publish successful" -ForegroundColor Green
        Write-Host "   Message: $($publishData.message)" -ForegroundColor Green
        Write-Host "   Strapi success: $($publishData.strapi.success)" -ForegroundColor Green
        if ($publishData.strapi.post_id) {
            Write-Host "   Post ID: $($publishData.strapi.post_id)" -ForegroundColor Cyan
        }
    } catch {
        Write-Host "❌ Publish failed" -ForegroundColor Red
        Write-Host "   Error: $_" -ForegroundColor Red
    }
}

# ============================================================================
# STEP 5: CHECK STRAPI POSTS
# ============================================================================

Write-Host "`n[STEP 5] Checking Strapi posts..." -ForegroundColor Yellow

try {
    $postsResponse = Invoke-WebRequest `
        -Uri "http://localhost:1337/api/posts?sort=-createdAt&pagination[limit]=5" `
        -TimeoutSec 5
    
    $postsData = $postsResponse.Content | ConvertFrom-Json
    $postCount = $postsData.meta.pagination.total
    
    Write-Host "✅ Strapi is responding" -ForegroundColor Green
    Write-Host "   Total posts: $postCount" -ForegroundColor Cyan
    
    if ($postCount -gt 0) {
        Write-Host "   Recent posts:" -ForegroundColor Cyan
        $postsData.data | Select-Object -First 3 | ForEach-Object {
            Write-Host "   - $($_.attributes.title) (ID: $($_.id))" -ForegroundColor Gray
        }
    }
} catch {
    Write-Host "⚠️  Cannot reach Strapi" -ForegroundColor Yellow
    Write-Host "   Make sure Strapi CMS is running: npm run develop --workspace=cms/strapi-main" -ForegroundColor Yellow
}

# ============================================================================
# STEP 6: FINAL SUMMARY
# ============================================================================

Write-Host "`n[SUMMARY]" -ForegroundColor Yellow
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray

if ($postCount -gt 0) {
    Write-Host "✅ PIPELINE WORKING: Posts are in Strapi!" -ForegroundColor Green
    Write-Host "   Check public-site to verify posts display correctly" -ForegroundColor Green
} elseif ($taskData.status -eq "completed") {
    Write-Host "⚠️  ISSUE: Task completed but no posts in Strapi" -ForegroundColor Yellow
    Write-Host "   This suggests the publish endpoint failed" -ForegroundColor Yellow
    Write-Host "   Check FastAPI logs for publishing errors" -ForegroundColor Yellow
} else {
    Write-Host "❌ ISSUE: Tasks never complete" -ForegroundColor Red
    Write-Host "   This means content generation isn't happening" -ForegroundColor Red
    Write-Host "   Check:" -ForegroundColor Yellow
    Write-Host "   1. FastAPI logs for task executor messages" -ForegroundColor Yellow
    Write-Host "   2. Is Ollama running? (ollama serve)" -ForegroundColor Yellow
    Write-Host "   3. Are there LLM/model router errors?" -ForegroundColor Yellow
}

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray

# ============================================================================
# NEXT STEPS
# ============================================================================

Write-Host "`nNEXT STEPS:" -ForegroundColor Cyan
Write-Host "1. Check PostgreSQL with: scripts\DIAGNOSE_PIPELINE.sql" -ForegroundColor Gray
Write-Host "2. Review FastAPI startup logs for errors" -ForegroundColor Gray
Write-Host "3. Verify Ollama is running if using local models" -ForegroundColor Gray
Write-Host "4. Check oversight-hub console (F12) for frontend errors" -ForegroundColor Gray
Write-Host "5. If posts exist in Strapi, check public-site display" -ForegroundColor Gray
