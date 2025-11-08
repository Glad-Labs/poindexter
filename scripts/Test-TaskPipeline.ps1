#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Troubleshoot the task creation pipeline
    
.DESCRIPTION
    Verifies each step of the pipeline:
    1. Backend is running and responsive
    2. PostgreSQL connection works
    3. Can create a test task
    4. Can fetch tasks from API
    5. Tasks exist in database
#>

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "🔍 Task Pipeline Troubleshooting Script" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan

# Configuration
$BACKEND_URL = "http://localhost:8000"
$API_TIMEOUT = 5
$TEST_TASK = @{
    task_name = "Pipeline Verification Test"
    topic = "Testing the task creation pipeline"
    primary_keyword = "pipeline test"
    target_audience = "Developers"
    category = "testing"
}

function Test-BackendHealth {
    Write-Host "`n📡 Step 1: Testing backend health..." -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri "$BACKEND_URL/api/health" -TimeoutSec $API_TIMEOUT -ErrorAction Stop
        $data = $response.Content | ConvertFrom-Json
        
        if ($data.status -eq "healthy") {
            Write-Host "✅ Backend is healthy" -ForegroundColor Green
            Write-Host "   Status: $($data.status)" -ForegroundColor Green
            return $true
        } else {
            Write-Host "⚠️  Backend responded but status is: $($data.status)" -ForegroundColor Yellow
            return $false
        }
    } catch {
        Write-Host "❌ Backend is not responding" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   → Make sure backend is running on port 8000" -ForegroundColor Yellow
        Write-Host "   → Run: npm run dev:cofounder" -ForegroundColor Yellow
        return $false
    }
}

function Test-APIHealth {
    Write-Host "`n📡 Step 2: Testing API endpoints..." -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri "$BACKEND_URL/api/tasks" `
            -Headers @{ "Authorization" = "Bearer test" } `
            -TimeoutSec $API_TIMEOUT `
            -ErrorAction Stop
        
        $data = $response.Content | ConvertFrom-Json
        Write-Host "✅ API is responding" -ForegroundColor Green
        Write-Host "   Tasks found: $($data.total)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "⚠️  API error (this may be normal)" -ForegroundColor Yellow
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

function Test-DatabaseConnection {
    Write-Host "`n🗄️  Step 3: Testing database connection..." -ForegroundColor Yellow
    
    $DatabaseUrl = [System.Environment]::GetEnvironmentVariable("DATABASE_URL")
    
    if (-not $DatabaseUrl) {
        Write-Host "❌ DATABASE_URL environment variable not set" -ForegroundColor Red
        Write-Host "   → Set DATABASE_URL or it will use SQLite" -ForegroundColor Yellow
        return $false
    }
    
    try {
        Write-Host "   Connection string set: $($DatabaseUrl.Substring(0, 20))..." -ForegroundColor Gray
        Write-Host "✅ Database configuration detected" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "❌ Database connection failed" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Test-CreateTask {
    Write-Host "`n📝 Step 4: Creating test task..." -ForegroundColor Yellow
    
    try {
        $body = $TEST_TASK | ConvertTo-Json
        
        $response = Invoke-WebRequest -Uri "$BACKEND_URL/api/tasks" `
            -Method POST `
            -Headers @{ 
                "Content-Type" = "application/json"
                "Authorization" = "Bearer test"
            } `
            -Body $body `
            -TimeoutSec $API_TIMEOUT `
            -ErrorAction Stop
        
        $data = $response.Content | ConvertFrom-Json
        
        if ($data.id) {
            Write-Host "✅ Task created successfully" -ForegroundColor Green
            Write-Host "   Task ID: $($data.id)" -ForegroundColor Green
            Write-Host "   Status: $($data.status)" -ForegroundColor Green
            return $data.id
        } else {
            Write-Host "⚠️  Task response missing ID" -ForegroundColor Yellow
            Write-Host "   Response: $data" -ForegroundColor Yellow
            return $null
        }
    } catch {
        Write-Host "❌ Failed to create task" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

function Test-FetchTasks {
    Write-Host "`n📋 Step 5: Fetching tasks from API..." -ForegroundColor Yellow
    
    try {
        $response = Invoke-WebRequest -Uri "$BACKEND_URL/api/tasks?limit=5" `
            -Headers @{ "Authorization" = "Bearer test" } `
            -TimeoutSec $API_TIMEOUT `
            -ErrorAction Stop
        
        $data = $response.Content | ConvertFrom-Json
        
        Write-Host "✅ Tasks fetched successfully" -ForegroundColor Green
        Write-Host "   Total tasks: $($data.total)" -ForegroundColor Green
        Write-Host "   Returned: $($data.tasks.Count)" -ForegroundColor Green
        
        if ($data.tasks.Count -gt 0) {
            Write-Host "   Latest task: $($data.tasks[0].task_name)" -ForegroundColor Green
        }
        
        return $data.tasks
    } catch {
        Write-Host "❌ Failed to fetch tasks" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

function Show-Results {
    Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "📊 Results Summary" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    
    Write-Host "`n✅ If all tests passed:" -ForegroundColor Green
    Write-Host "   1. Refresh Oversight Hub in browser" -ForegroundColor Green
    Write-Host "   2. Clear browser cache (DevTools → Application → Clear)" -ForegroundColor Green
    Write-Host "   3. Create a new task" -ForegroundColor Green
    Write-Host "   4. It should appear in the task list immediately" -ForegroundColor Green
    
    Write-Host "`n❌ If tests failed:" -ForegroundColor Red
    Write-Host "   1. Check backend logs in terminal" -ForegroundColor Red
    Write-Host "   2. Verify DATABASE_URL is set" -ForegroundColor Red
    Write-Host "   3. Restart backend: npm run dev:cofounder" -ForegroundColor Red
    Write-Host "   4. Check PostgreSQL is running" -ForegroundColor Red
    
    Write-Host "`n📝 To debug further:" -ForegroundColor Yellow
    Write-Host "   • Backend logs: Terminal where backend is running" -ForegroundColor Yellow
    Write-Host "   • Browser console: Press F12 → Console tab" -ForegroundColor Yellow
    Write-Host "   • Database: psql `$DATABASE_URL -c 'SELECT * FROM tasks LIMIT 5;'" -ForegroundColor Yellow
}

# Run all tests
$backendOK = Test-BackendHealth
$apiOK = Test-APIHealth
$dbOK = Test-DatabaseConnection
$taskID = $null

if ($backendOK) {
    $taskID = Test-CreateTask
}

if ($taskID) {
    $tasks = Test-FetchTasks
}

Show-Results

Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
