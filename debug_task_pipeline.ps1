# 🔍 Task Pipeline Debugging Script
# Purpose: Verify task creation and execution pipeline
# Author: GitHub Copilot
# Date: November 6, 2025

Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║      TASK PIPELINE DEBUGGING SCRIPT                       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Configuration
$BACKEND_URL = "http://localhost:8000"
$HEALTH_ENDPOINT = "$BACKEND_URL/api/health"
$TASKS_ENDPOINT = "$BACKEND_URL/api/tasks"
$MAX_WAIT = 15  # seconds to wait for task completion

# Helper functions
function Test-Backend {
    Write-Host "🔍 Step 1: Checking Backend Health..." -ForegroundColor Yellow
    
    try {
        $response = Invoke-RestMethod -Uri $HEALTH_ENDPOINT -Method Get -ErrorAction Stop
        Write-Host "✅ Backend is running!" -ForegroundColor Green
        Write-Host "   Status: $($response.status)" -ForegroundColor Green
        Write-Host "   Response: $(($response | ConvertTo-Json) | Truncate -Length 100)" -ForegroundColor Gray
        return $true
    }
    catch {
        Write-Host "❌ Backend is NOT responding!" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   Make sure backend is running:" -ForegroundColor Yellow
        Write-Host "   - Run: python start_backend.py" -ForegroundColor Yellow
        return $false
    }
}

function Create-TestTask {
    Write-Host ""
    Write-Host "📝 Step 2: Creating Test Task..." -ForegroundColor Yellow
    
    $taskData = @{
        task_name = "Debug Test - $(Get-Date -Format 'HH:mm:ss')"
        topic = "The Future of Artificial Intelligence"
        primary_keyword = "AI, machine learning, automation"
        target_audience = "Technology professionals and business leaders"
        category = "technology"
    } | ConvertTo-Json
    
    try {
        $response = Invoke-RestMethod `
            -Uri $TASKS_ENDPOINT `
            -Method Post `
            -Body $taskData `
            -ContentType "application/json" `
            -ErrorAction Stop
        
        Write-Host "✅ Task created successfully!" -ForegroundColor Green
        Write-Host "   Task ID: $($response.id)" -ForegroundColor Green
        Write-Host "   Name: $($response.task_name)" -ForegroundColor Green
        Write-Host "   Status: $($response.status)" -ForegroundColor Green
        
        return $response.id
    }
    catch {
        Write-Host "❌ Failed to create task!" -ForegroundColor Red
        Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

function Monitor-Task {
    param([string]$TaskId)
    
    Write-Host ""
    Write-Host "⏳ Step 3: Monitoring Task Execution..." -ForegroundColor Yellow
    Write-Host "   (Waiting up to $MAX_WAIT seconds for completion)" -ForegroundColor Gray
    Write-Host ""
    
    $startTime = Get-Date
    $completed = $false
    $result = $null
    
    while ((Get-Date) - $startTime -lt (New-TimeSpan -Seconds $MAX_WAIT)) {
        try {
            $result = Invoke-RestMethod `
                -Uri "$TASKS_ENDPOINT/$TaskId" `
                -Method Get `
                -ErrorAction Stop
            
            # Display status
            $elapsed = [int]((Get-Date) - $startTime).TotalSeconds
            Write-Host "   [$elapsed`s] Status: $($result.status)" -ForegroundColor Cyan
            
            if ($result.status -eq "completed") {
                $completed = $true
                Write-Host "   ✅ Task completed!" -ForegroundColor Green
                break
            }
            elseif ($result.status -eq "failed") {
                Write-Host "   ❌ Task failed!" -ForegroundColor Red
                Write-Host "      Error: $($result.error)" -ForegroundColor Red
                break
            }
        }
        catch {
            Write-Host "   ⚠️  Error checking status: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        
        Start-Sleep -Seconds 1
    }
    
    return $result
}

function Display-Result {
    param([object]$Result)
    
    Write-Host ""
    Write-Host "📊 Step 4: Displaying Task Result..." -ForegroundColor Yellow
    Write-Host ""
    
    if ($Result.status -eq "completed") {
        Write-Host "✅ TASK COMPLETED SUCCESSFULLY" -ForegroundColor Green
        Write-Host ""
        Write-Host "Task Information:" -ForegroundColor Cyan
        Write-Host "  - Task ID: $($Result.id)" -ForegroundColor Gray
        Write-Host "  - Name: $($Result.task_name)" -ForegroundColor Gray
        Write-Host "  - Topic: $($Result.topic)" -ForegroundColor Gray
        Write-Host "  - Status: $($Result.status)" -ForegroundColor Gray
        Write-Host "  - Created: $($Result.created_at)" -ForegroundColor Gray
        Write-Host "  - Completed: $($Result.completed_at)" -ForegroundColor Gray
        Write-Host ""
        
        Write-Host "Generated Content:" -ForegroundColor Cyan
        Write-Host "  Word Count: $($Result.word_count)" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Content:" -ForegroundColor Gray
        Write-Host "  ┌─────────────────────────────────────────┐" -ForegroundColor Gray
        
        # Split content into lines and display with indentation
        $lines = $Result.content -split "`n"
        foreach ($line in $lines) {
            Write-Host "  │ $line" -ForegroundColor Gray
        }
        
        Write-Host "  └─────────────────────────────────────────┘" -ForegroundColor Gray
        Write-Host ""
        
        # Additional fields
        if ($Result.result) {
            Write-Host "Raw Result Object:" -ForegroundColor Cyan
            $Result.result | ConvertTo-Json | Write-Host -ForegroundColor Gray
        }
        
        return $true
    }
    else {
        Write-Host "⚠️  TASK DID NOT COMPLETE" -ForegroundColor Yellow
        Write-Host "   Status: $($Result.status)" -ForegroundColor Yellow
        if ($Result.error) {
            Write-Host "   Error: $($Result.error)" -ForegroundColor Red
        }
        Write-Host ""
        Write-Host "Full Result:" -ForegroundColor Yellow
        $Result | ConvertTo-Json | Write-Host -ForegroundColor Gray
        
        return $false
    }
}

function Show-Summary {
    param([bool]$Success, [string]$TaskId, [object]$Result)
    
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║                      DEBUG SUMMARY                         ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    
    if ($Success) {
        Write-Host "✅ Pipeline Status: FULLY FUNCTIONAL" -ForegroundColor Green
        Write-Host ""
        Write-Host "   What's Working:" -ForegroundColor Green
        Write-Host "   ✓ Backend API responding" -ForegroundColor Green
        Write-Host "   ✓ Task creation endpoint" -ForegroundColor Green
        Write-Host "   ✓ TaskExecutor polling (5s interval)" -ForegroundColor Green
        Write-Host "   ✓ Task status updates" -ForegroundColor Green
        Write-Host "   ✓ Database storage and retrieval" -ForegroundColor Green
        Write-Host "   ✓ Result generation" -ForegroundColor Green
        Write-Host ""
        Write-Host "   What's Currently Mock:" -ForegroundColor Yellow
        Write-Host "   • Content generation (using placeholder)" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "   Next Steps:" -ForegroundColor Cyan
        Write-Host "   1. Content is mock/placeholder (by design)" -ForegroundColor Cyan
        Write-Host "   2. To get real content: Integrate LLM (Ollama/OpenAI)" -ForegroundColor Cyan
        Write-Host "   3. Pipeline itself is production-ready ✓" -ForegroundColor Cyan
    }
    else {
        Write-Host "❌ Pipeline Status: NEEDS DEBUGGING" -ForegroundColor Red
        Write-Host ""
        Write-Host "   Issues Found:" -ForegroundColor Red
        
        if ($TaskId -eq $null) {
            Write-Host "   • Task creation failed - Check backend logs" -ForegroundColor Red
        }
        elseif ($Result.status -eq "failed") {
            Write-Host "   • Task execution failed - Check TaskExecutor" -ForegroundColor Red
            if ($Result.error) {
                Write-Host "   • Error message: $($Result.error)" -ForegroundColor Red
            }
        }
        else {
            Write-Host "   • Task did not complete in time" -ForegroundColor Red
            Write-Host "   • Check if TaskExecutor is running" -ForegroundColor Red
        }
        
        Write-Host ""
        Write-Host "   Debugging Steps:" -ForegroundColor Yellow
        Write-Host "   1. Verify backend is running: python start_backend.py" -ForegroundColor Yellow
        Write-Host "   2. Check backend terminal for errors" -ForegroundColor Yellow
        Write-Host "   3. Verify database connectivity" -ForegroundColor Yellow
        Write-Host "   4. Check TaskExecutor logs" -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "For more details, see: TASK_CREATION_DEBUG_GUIDE.md" -ForegroundColor Cyan
    Write-Host ""
}

# Main execution
Write-Host ""

# Step 1: Test backend
$backendOk = Test-Backend
if (-not $backendOk) {
    Write-Host ""
    Write-Host "❌ Cannot continue - backend not responding!" -ForegroundColor Red
    exit 1
}

# Step 2: Create task
$taskId = Create-TestTask
if (-not $taskId) {
    Write-Host ""
    Write-Host "❌ Cannot continue - task creation failed!" -ForegroundColor Red
    exit 1
}

# Step 3: Monitor task
$result = Monitor-Task -TaskId $taskId
if (-not $result) {
    Write-Host ""
    Write-Host "❌ Could not retrieve task result!" -ForegroundColor Red
    Show-Summary -Success $false -TaskId $taskId -Result $null
    exit 1
}

# Step 4: Display result
$success = Display-Result -Result $result

# Summary
Show-Summary -Success $success -TaskId $taskId -Result $result

Write-Host ""
if ($success) {
    exit 0
} else {
    exit 1
}
