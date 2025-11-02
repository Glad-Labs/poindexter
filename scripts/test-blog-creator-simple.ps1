#!/usr/bin/env pwsh
# Simple API test for BlogPostCreator endpoints

$ApiUrl = "http://127.0.0.1:8000"
$Passed = 0
$Failed = 0

Write-Host "Testing BlogPostCreator API Endpoints`n" -ForegroundColor Cyan

# Test 1: Health Check
Write-Host "TEST 1: Backend Health Check" -ForegroundColor Green
try {
    $response = Invoke-WebRequest -Uri "$ApiUrl/api/health" -UseBasicParsing -ErrorAction Stop
    $json = $response.Content | ConvertFrom-Json
    Write-Host "  Result: PASS - Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "  Response status: $($json.status)" -ForegroundColor Green
    $Passed++
}
catch {
    Write-Host "  Result: FAIL - $($_.Exception.Message)" -ForegroundColor Red
    $Failed++
}
Write-Host ""

# Test 2: Create Blog Post
Write-Host "TEST 2: Create Blog Post (POST /api/content/blog-posts)" -ForegroundColor Green
try {
    $payload = @{
        topic = "Test Blog Post"
        style = "technical"
        tone = "professional"
        target_length = 1500
        tags = @("test", "ai")
        categories = @("tech")
        generate_featured_image = $true
        enhanced = $false
        publish_mode = "draft"
        target_environment = "production"
    } | ConvertTo-Json
    
    $response = Invoke-WebRequest `
        -Uri "$ApiUrl/api/content/blog-posts" `
        -Method POST `
        -Body $payload `
        -ContentType "application/json" `
        -UseBasicParsing `
        -ErrorAction Stop
    
    $json = $response.Content | ConvertFrom-Json
    $taskId = $json.task_id
    
    Write-Host "  Result: PASS - Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "  Task ID: $taskId" -ForegroundColor Green
    Write-Host "  Task Status: $($json.status)" -ForegroundColor Green
    $Passed++
    
    # Test 3: Get Task Status
    Write-Host ""
    Write-Host "TEST 3: Get Task Status (GET /api/content/blog-posts/tasks/{taskId})" -ForegroundColor Green
    try {
        $statusResponse = Invoke-WebRequest `
            -Uri "$ApiUrl/api/content/blog-posts/tasks/$taskId" `
            -UseBasicParsing `
            -ErrorAction Stop
        
        $statusJson = $statusResponse.Content | ConvertFrom-Json
        Write-Host "  Result: PASS - Status: $($statusResponse.StatusCode)" -ForegroundColor Green
        Write-Host "  Task Status: $($statusJson.status)" -ForegroundColor Green
        $Passed++
    }
    catch {
        Write-Host "  Result: FAIL - $($_.Exception.Message)" -ForegroundColor Red
        $Failed++
    }
}
catch {
    Write-Host "  Result: FAIL - $($_.Exception.Message)" -ForegroundColor Red
    $Failed++
}
Write-Host ""

# Test 4: List Drafts
Write-Host "TEST 4: List Blog Post Drafts (GET /api/content/blog-posts/drafts)" -ForegroundColor Green
try {
    $response = Invoke-WebRequest `
        -Uri "$ApiUrl/api/content/blog-posts/drafts" `
        -UseBasicParsing `
        -ErrorAction Stop
    
    $json = $response.Content | ConvertFrom-Json
    Write-Host "  Result: PASS - Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "  Drafts count: $($json.drafts.Count)" -ForegroundColor Green
    $Passed++
}
catch {
    Write-Host "  Result: FAIL - $($_.Exception.Message)" -ForegroundColor Red
    $Failed++
}
Write-Host ""

# Test 5: Invalid Topic
Write-Host "TEST 5: Invalid Topic Error Handling" -ForegroundColor Green
try {
    $payload = @{
        topic = "x"
        style = "technical"
    } | ConvertTo-Json
    
    $response = Invoke-WebRequest `
        -Uri "$ApiUrl/api/content/blog-posts" `
        -Method POST `
        -Body $payload `
        -ContentType "application/json" `
        -UseBasicParsing `
        -ErrorAction Stop
    
    Write-Host "  Result: FAIL - Should have rejected invalid topic" -ForegroundColor Red
    $Failed++
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.Value__
    if ($statusCode -eq 400 -or $statusCode -eq 422) {
        Write-Host "  Result: PASS - Correctly rejected with status $statusCode" -ForegroundColor Green
        $Passed++
    }
    else {
        Write-Host "  Result: FAIL - Unexpected error: $($_.Exception.Message)" -ForegroundColor Red
        $Failed++
    }
}
Write-Host ""

# Summary
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Test Summary" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Passed: $Passed" -ForegroundColor Green
Write-Host "Failed: $Failed" -ForegroundColor Red

if ($Failed -eq 0) {
    Write-Host "`nAll tests passed!" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "`nSome tests failed!" -ForegroundColor Red
    exit 1
}
