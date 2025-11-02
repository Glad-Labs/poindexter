#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Quick API test script for BlogPostCreator integration testing
.DESCRIPTION
    Tests the backend endpoints that BlogPostCreator component depends on
.EXAMPLE
    .\test-blog-creator-api.ps1
#>

$ErrorActionPreference = "Stop"

# Color codes for output
$Green = "`e[32m"
$Red = "`e[31m"
$Yellow = "`e[33m"
$Blue = "`e[34m"
$Reset = "`e[0m"

Write-Host "${Blue}╔════════════════════════════════════════════════════╗${Reset}"
Write-Host "${Blue}║     BlogPostCreator API Integration Test          ║${Reset}"
Write-Host "${Blue}╚════════════════════════════════════════════════════╝${Reset}`n"

# Configuration
$API_BASE_URL = "http://127.0.0.1:8000"
$TESTS_PASSED = 0
$TESTS_FAILED = 0

# Helper function to make API calls
function Test-ApiEndpoint {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Endpoint,
        [object]$Body = $null
    )
    
    Write-Host "${Blue}[TEST]${Reset} $Name"
    Write-Host "       ${Yellow}$Method $Endpoint${Reset}"
    
    try {
        $Uri = "$API_BASE_URL$Endpoint"
        $Params = @{
            Uri             = $Uri
            Method          = $Method
            UseBasicParsing = $true
            ErrorAction     = "Stop"
        }
        
        if ($Body) {
            $Params["Body"] = ($Body | ConvertTo-Json -Depth 10)
            $Params["ContentType"] = "application/json"
        }
        
        $Response = Invoke-WebRequest @Params
        $Content = $Response.Content | ConvertFrom-Json
        
        Write-Host "       ${Green}✓ SUCCESS${Reset} (Status: $($Response.StatusCode))"
        Write-Host "       Response: $($Content | ConvertTo-Json -Depth 2 | Select-Object -First 5)"
        Write-Host ""
        
        $script:TESTS_PASSED++
        return $Content
    }
    catch {
        Write-Host "       ${Red}✗ FAILED${Reset}"
        Write-Host "       Error: $($_.Exception.Message)"
        Write-Host ""
        
        $script:TESTS_FAILED++
        return $null
    }
}

# Test 1: Health Check
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
Write-Host "${Green}TEST SUITE 1: Backend Infrastructure${Reset}"
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"

Test-ApiEndpoint -Name "Backend Health Check" -Method "GET" -Endpoint "/api/health"

# Test 2: Create Blog Post (Minimal)
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
Write-Host "${Green}TEST SUITE 2: Blog Post Creation${Reset}"
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"

$CreateBlogPostPayload = @{
    topic                  = "Test: How to optimize AI costs in production"
    style                  = "technical"
    tone                   = "professional"
    target_length          = 1500
    tags                   = @("AI", "cost-optimization", "test")
    categories             = @("Technical Guides")
    generate_featured_image = $true
    enhanced               = $false
    publish_mode           = "draft"
    target_environment     = "production"
}

$BlogPostResponse = Test-ApiEndpoint `
    -Name "Create Blog Post (Basic)" `
    -Method "POST" `
    -Endpoint "/api/content/blog-posts" `
    -Body $CreateBlogPostPayload

if ($BlogPostResponse) {
    $TaskId = $BlogPostResponse.task_id
    Write-Host "${Blue}[INFO]${Reset} Got Task ID: $TaskId`n"
    
    # Test 3: Check Task Status (Immediate - should be pending)
    Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
    Write-Host "${Green}TEST SUITE 3: Task Status Polling${Reset}"
    Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"
    
    $StatusResponse = Test-ApiEndpoint `
        -Name "Get Task Status (Immediate)" `
        -Method "GET" `
        -Endpoint "/api/content/blog-posts/tasks/$TaskId"
    
    if ($StatusResponse) {
        Write-Host "${Blue}[INFO]${Reset} Task Status: $($StatusResponse.status)`n"
    }
    
    # Test 4: List Drafts
    Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
    Write-Host "${Green}TEST SUITE 4: Blog Post Drafts${Reset}"
    Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"
    
    Test-ApiEndpoint `
        -Name "List Blog Post Drafts" `
        -Method "GET" `
        -Endpoint "/api/content/blog-posts/drafts"
}

# Test 5: Invalid Input (Error Handling)
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
Write-Host "${Green}TEST SUITE 5: Error Handling${Reset}"
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"

$InvalidPayload = @{
    topic  = "x"  # Too short - should fail
    style  = "technical"
    tone   = "professional"
}

Write-Host "${Blue}[TEST]${Reset} Invalid Topic (Expected to Fail)"
Write-Host "       ${Yellow}POST /api/content/blog-posts${Reset}"

try {
    $Response = Invoke-WebRequest `
        -Uri "$API_BASE_URL/api/content/blog-posts" `
        -Method "POST" `
        -Body ($InvalidPayload | ConvertTo-Json) `
        -ContentType "application/json" `
        -UseBasicParsing `
        -ErrorAction Stop
    
    Write-Host "       ${Red}✗ UNEXPECTED SUCCESS${Reset}"
    Write-Host "       Should have failed with validation error"
    $script:TESTS_FAILED++
}
catch {
    $StatusCode = $_.Exception.Response.StatusCode.Value__
    if ($StatusCode -eq 400 -or $StatusCode -eq 422) {
        Write-Host "       ${Green}✓ CORRECTLY REJECTED${Reset} (Status: $StatusCode)"
        Write-Host "       Error: $($_.Exception.Message)"
        $script:TESTS_PASSED++
    }
    else {
        Write-Host "       ${Yellow}⚠ UNEXPECTED ERROR${Reset} (Status: $StatusCode)"
        Write-Host "       Error: $($_.Exception.Message)"
        $script:TESTS_FAILED++
    }
}
Write-Host ""

# Test 6: Get Non-Existent Task (Error Handling)
Write-Host "${Blue}[TEST]${Reset} Get Non-Existent Task (Expected to Fail)"
Write-Host "       ${Yellow}GET /api/content/blog-posts/tasks/invalid-task-id${Reset}"

try {
    $Response = Invoke-WebRequest `
        -Uri "$API_BASE_URL/api/content/blog-posts/tasks/invalid-task-id" `
        -Method "GET" `
        -UseBasicParsing `
        -ErrorAction Stop
    
    Write-Host "       ${Red}✗ UNEXPECTED SUCCESS${Reset}"
    Write-Host "       Should have failed with 404"
    $script:TESTS_FAILED++
}
catch {
    $StatusCode = $_.Exception.Response.StatusCode.Value__
    if ($StatusCode -eq 404) {
        Write-Host "       ${Green}✓ CORRECTLY REJECTED${Reset} (Status: $StatusCode)"
        Write-Host "       Error: Task not found"
        $script:TESTS_PASSED++
    }
    else {
        Write-Host "       ${Yellow}⚠ UNEXPECTED ERROR${Reset} (Status: $StatusCode)"
        Write-Host "       Error: $($_.Exception.Message)"
        $script:TESTS_FAILED++
    }
}
Write-Host ""

# Summary
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}"
Write-Host "${Green}TEST RESULTS SUMMARY${Reset}"
Write-Host "${Green}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${Reset}`n"

Write-Host "${Green}✓ Passed: $TESTS_PASSED${Reset}"
Write-Host "${Red}✗ Failed: $TESTS_FAILED${Reset}"

if ($TESTS_FAILED -eq 0) {
    Write-Host "`n${Green}🎉 ALL TESTS PASSED!${Reset}`n"
    exit 0
}
else {
    Write-Host "`n${Red}❌ SOME TESTS FAILED${Reset}`n"
    exit 1
}
