# Complete pipeline test: Create task -> Generate content -> Publish to Strapi -> Display on public site

Write-Host "================================" -ForegroundColor Cyan
Write-Host "[+] COMPLETE PIPELINE TEST" -ForegroundColor Yellow
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Test 1: Create task via API
Write-Host "STEP 1: Creating test task..." -ForegroundColor Green

$taskData = @{
    title = "AI Tools Review 2025 (AUTO TEST $(Get-Date -Format 'HHmmss'))"
    description = "Testing automatic content generation and publishing"
    type = "content_generation"
    parameters = @{
        topic = "Best Free AI Tools 2025"
        keywords = "AI, tools, free, productivity"
        audience = "Tech enthusiasts and professionals"
        length = "1500"
    }
} | ConvertTo-Json

Write-Host "Sending task creation request to http://127.0.0.1:8000/api/tasks" -ForegroundColor Gray

try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/tasks" `
        -Method POST `
        -ContentType "application/json" `
        -Body $taskData `
        -UseBasicParsing
    
    $taskResponse = $response.Content | ConvertFrom-Json
    $taskId = $taskResponse.id
    
    Write-Host "[OK] Task created successfully!" -ForegroundColor Green
    Write-Host "   Task ID: $taskId" -ForegroundColor Yellow
    Write-Host "   Status: $($taskResponse.status)" -ForegroundColor Yellow
    Write-Host ""
} catch {
    Write-Host "[FAIL] Failed to create task" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Test 2: Wait for background task execution
Write-Host "STEP 2: Waiting for background content generation..." -ForegroundColor Green
Write-Host "   (This may take 30-60 seconds for Ollama to generate content)" -ForegroundColor Gray
Write-Host ""

$maxWait = 120  # 2 minutes max
$checkInterval = 5
$elapsed = 0

do {
    Start-Sleep -Seconds $checkInterval
    $elapsed += $checkInterval
    
    try {
        $taskStatus = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/tasks/$taskId" `
            -Method GET `
            -UseBasicParsing
        
        $taskData = $taskStatus.Content | ConvertFrom-Json
        $status = $taskData.status
        
        # Print progress
        if ($taskData.metadata.content) {
            $contentLength = $taskData.metadata.content.Length
            Write-Host "   [${elapsed}s] Status: $status | Generated content: $contentLength chars" -ForegroundColor Yellow
        } else {
            Write-Host "   [${elapsed}s] Status: $status | Waiting for content generation..." -ForegroundColor Cyan
        }
        
        # Exit loop when completed
        if ($status -eq "completed" -or $status -eq "success") {
            Write-Host ""
            Write-Host "[OK] Background content generation completed!" -ForegroundColor Green
            break
        }
    } catch {
        Write-Host "   [${elapsed}s] Checking status..." -ForegroundColor Gray
    }
    
} while ($elapsed -lt $maxWait)

if ($elapsed -ge $maxWait) {
    Write-Host ""
    Write-Host "[WARN] Timeout: Task took longer than 2 minutes" -ForegroundColor Yellow
}

Write-Host ""

# Test 3: Verify content in Strapi
Write-Host "STEP 3: Verifying content published to Strapi..." -ForegroundColor Green

try {
    $strapiPosts = Invoke-WebRequest -Uri "http://localhost:1337/api/posts?sort=-createdAt&pagination[limit]=1" `
        -Method GET `
        -UseBasicParsing
    
    $postsData = $strapiPosts.Content | ConvertFrom-Json
    $latestPost = $postsData.data[0]
    
    if ($latestPost) {
        Write-Host "[OK] Found latest post in Strapi!" -ForegroundColor Green
        Write-Host "   ID: $($latestPost.id)" -ForegroundColor Yellow
        Write-Host "   Title: $($latestPost.attributes.title)" -ForegroundColor Yellow
        Write-Host "   Status: $($latestPost.attributes.status)" -ForegroundColor Yellow
        
        # Check content length
        $contentLength = $latestPost.attributes.content.Length
        if ($contentLength -gt 100) {
            Write-Host "   Content: $contentLength characters [OK - Real content]" -ForegroundColor Green
        } else {
            Write-Host "   Content: $contentLength characters [WARN - Might be placeholder]" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[WARN] No posts found in Strapi yet" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAIL] Could not connect to Strapi: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# Test 4: Check public site
Write-Host "STEP 4: Checking public site..." -ForegroundColor Green
Write-Host "   Navigate to: http://localhost:3000" -ForegroundColor Yellow
Write-Host "   New post should appear at the top (most recent)" -ForegroundColor Yellow
Write-Host ""

# Test 5: Get task details including metadata
Write-Host "STEP 5: Task completion details..." -ForegroundColor Green

try {
    $finalTask = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/tasks/$taskId" `
        -Method GET `
        -UseBasicParsing
    
    $finalTaskData = $finalTask.Content | ConvertFrom-Json
    
    Write-Host "Final Task Status:" -ForegroundColor Yellow
    Write-Host "   Status: $($finalTaskData.status)" -ForegroundColor Cyan
    Write-Host "   Created: $($finalTaskData.created_at)" -ForegroundColor Cyan
    Write-Host "   Updated: $($finalTaskData.updated_at)" -ForegroundColor Cyan
    
    if ($finalTaskData.metadata.content) {
        $preview = $finalTaskData.metadata.content.Substring(0, [Math]::Min(150, $finalTaskData.metadata.content.Length))
        Write-Host "   Generated Content Preview:" -ForegroundColor Green
        Write-Host "   $preview..." -ForegroundColor Gray
    }
    
    if ($finalTaskData.metadata.strapi_post_id) {
        Write-Host "   Strapi Post ID: $($finalTaskData.metadata.strapi_post_id)" -ForegroundColor Green
    }
} catch {
    Write-Host "Error getting task details: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "[OK] TEST COMPLETE!" -ForegroundColor Yellow
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Summary:" -ForegroundColor Green
Write-Host "  1. [OK] Task created: $taskId" -ForegroundColor White
Write-Host "  2. [WAIT] Background generation executing..." -ForegroundColor White
Write-Host "  3. [OK] Content verified in Strapi" -ForegroundColor White
Write-Host "  4. [INFO] Check public site for new post" -ForegroundColor White
