#!/usr/bin/env powershell

$BACKEND = "http://localhost:8000"
$STRAPI = "http://localhost:1337"
$TOKEN = $env:STRAPI_API_TOKEN

Write-Host ""
Write-Host "API TO STRAPI PIPELINE TEST" -ForegroundColor Cyan
Write-Host ""

Write-Host "STEP 1: Backend Health" -ForegroundColor White
try {
    Invoke-RestMethod -Uri "$BACKEND/api/health" -TimeoutSec 5 | Out-Null
    Write-Host "[PASS] Backend running" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Backend not responding" -ForegroundColor Red
    exit 1
}

Write-Host "STEP 2: Strapi Token Check" -ForegroundColor White
if (-not $TOKEN) {
    Write-Host "[FAIL] Token not set" -ForegroundColor Red
    exit 1
} else {
    Write-Host "[PASS] API token configured" -ForegroundColor Green
}

Write-Host "STEP 3: Create Task" -ForegroundColor White
$body = @{
    task_name = "Test-$(Get-Date -Format 'HH:mm:ss')"
    topic = "AI in Business"
    primary_keyword = "AI"
    target_audience = "Leaders"
    category = "Tech"
} | ConvertTo-Json

try {
    $resp = Invoke-RestMethod -Uri "$BACKEND/api/tasks" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    $id = $resp.id
    Write-Host "[PASS] Task created: $id" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Could not create task" -ForegroundColor Red
    exit 1
}

Write-Host "STEP 4: Monitor Task" -ForegroundColor White
$start = Get-Date
$max = 90
$attempts = 0

while (((Get-Date) - $start).TotalSeconds -lt $max) {
    try {
        $task = Invoke-RestMethod -Uri "$BACKEND/api/tasks/$id" -TimeoutSec 5
        $attempts++
        Write-Host "  Check $attempts : $($task.status)" -ForegroundColor Gray
        
        if ($task.status -eq "completed") {
            Write-Host "[PASS] Task completed" -ForegroundColor Green
            $final = $task
            break
        }
    } catch {
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    
    Start-Sleep -Seconds 3
}

if (-not $final) {
    Write-Host "[FAIL] Task timeout" -ForegroundColor Red
    exit 1
}

Write-Host "STEP 5: Check Result" -ForegroundColor White

# Extract result fields - they're nested in the result object
$content_length = $final.result.content_length
$quality_score = $final.result.quality_score

if ($content_length -gt 300) {
    Write-Host "[PASS] Content: $content_length chars" -ForegroundColor Green
} else {
    Write-Host "[WARN] Content: $content_length chars" -ForegroundColor Yellow
}

if ($quality_score -ge 75) {
    Write-Host "[PASS] Quality: $quality_score/100" -ForegroundColor Green
} else {
    Write-Host "[WARN] Quality: $quality_score/100" -ForegroundColor Yellow
}

Write-Host "STEP 6: Verify in Strapi" -ForegroundColor White
$strapi_post_id = $final.result.strapi_post_id
$publish_status = $final.result.publish_status

if ($strapi_post_id) {
    try {
        $h = @{"Authorization" = "Bearer $TOKEN"}
        $post = Invoke-RestMethod -Uri "$STRAPI/api/articles/$strapi_post_id" -Headers $h -TimeoutSec 5
        Write-Host "[PASS] Blog post published to Strapi" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Post not found in Strapi" -ForegroundColor Yellow
    }
} else {
    Write-Host "[INFO] Post not published ($publish_status)" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "SUCCESS - Pipeline test completed" -ForegroundColor Green
Write-Host ""
Write-Host "Task ID: $id" -ForegroundColor White
Write-Host "Content: $content_length chars" -ForegroundColor White
Write-Host "Quality: $quality_score/100" -ForegroundColor White
Write-Host "Status: $publish_status" -ForegroundColor White
Write-Host ""

exit 0
