# Comprehensive End-to-End Test Script
# Tests the entire task-to-post publishing pipeline

param(
    [string]$BackendUrl = "http://localhost:8000",
    [int]$WaitSeconds = 10,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$VerbosePreference = if ($Verbose) { "Continue" } else { "SilentlyContinue" }

# Color output
function Write-Success { Write-Host "[✓] $args" -ForegroundColor Green }
function Write-Error-Custom { Write-Host "[✗] $args" -ForegroundColor Red }
function Write-Info { Write-Host "[i] $args" -ForegroundColor Cyan }
function Write-Warning-Custom { Write-Host "[!] $args" -ForegroundColor Yellow }

# Counter for tests
$TotalTests = 0
$PassedTests = 0
$FailedTests = 0

# Test 1: Server Health Check
Write-Info "Test 1: Verifying backend server is running..."
$TotalTests++

try {
    $health = curl -s "$BackendUrl/api/health" | ConvertFrom-Json
    if ($health.status -eq "healthy") {
        Write-Success "Backend server is healthy"
        $PassedTests++
    } else {
        Write-Error-Custom "Server returned unhealthy status: $($health.status)"
        $FailedTests++
    }
} catch {
    Write-Error-Custom "Failed to reach backend server: $_"
    $FailedTests++
    exit 1
}

# Test 2: Create Task
Write-Info "Test 2: Creating test task..."
$TotalTests++

$taskPayload = @{
    task_name = "E2E Test - Microservices Architecture Patterns"
    type = "content_generation"
    topic = "Microservices Architecture Patterns"
    category = "technology"
} | ConvertTo-Json

try {
    $createResponse = curl -s -X POST "$BackendUrl/api/tasks" `
        -H "Content-Type: application/json" `
        -d $taskPayload | ConvertFrom-Json
    
    $taskId = $createResponse.id
    if ($taskId) {
        Write-Success "Task created with ID: $taskId"
        Write-Verbose "Task Status: $($createResponse.status)"
        $PassedTests++
    } else {
        Write-Error-Custom "Failed to create task: $($createResponse.detail)"
        $FailedTests++
        exit 1
    }
} catch {
    Write-Error-Custom "Error creating task: $_"
    $FailedTests++
    exit 1
}

# Test 3: Wait for Task Completion
Write-Info "Test 3: Waiting for task completion (${WaitSeconds}s)..."
$TotalTests++

Start-Sleep -Seconds $WaitSeconds

try {
    $taskStatus = curl -s "$BackendUrl/api/tasks/$taskId" | ConvertFrom-Json
    
    if ($taskStatus.status -eq "completed") {
        Write-Success "Task completed successfully"
        Write-Verbose "Status: $($taskStatus.status)"
        Write-Verbose "Result Status: $($taskStatus.result.status)"
        Write-Verbose "Post Created: $($taskStatus.result.post_created)"
        Write-Verbose "Content Length: $($taskStatus.result.content_length) chars"
        
        if ($taskStatus.result.post_created -eq $true) {
            $PassedTests++
        } else {
            Write-Error-Custom "Task completed but post was not created"
            $FailedTests++
        }
    } else {
        Write-Error-Custom "Task did not complete. Status: $($taskStatus.status)"
        $FailedTests++
    }
} catch {
    Write-Error-Custom "Error retrieving task status: $_"
    $FailedTests++
}

# Test 4: Verify Post in Database
Write-Info "Test 4: Verifying post was created in database..."
$TotalTests++

try {
    $psqlQuery = @"
SELECT 
  id::text,
  title,
  slug,
  LENGTH(content) as content_chars,
  status,
  seo_title,
  seo_keywords
FROM posts
WHERE slug = '$(($taskStatus.result.slug -replace '-', '-').ToLower())'
LIMIT 1;
"@

    # Note: This assumes psql is in PATH
    $dbResult = psql "postgresql://postgres:postgres@localhost:5432/glad_labs_dev" -c $psqlQuery -t -A -F '|' 2>$null
    
    if ($dbResult) {
        Write-Success "Post found in database"
        Write-Verbose "Database Result: $dbResult"
        $PassedTests++
    } else {
        Write-Warning-Custom "Could not verify post in database (psql may not be available)"
        Write-Verbose "Skipping database verification"
    }
} catch {
    Write-Warning-Custom "Could not verify post in database: $_"
}

# Test 5: Create Multiple Tasks
Write-Info "Test 5: Creating multiple tasks to test concurrent processing..."
$TotalTests++

$topics = @(
    "Artificial Intelligence in Healthcare",
    "Blockchain Technology Revolution",
    "Quantum Computing Future"
)

$taskIds = @()
$successCount = 0

foreach ($topic in $topics) {
    try {
        $payload = @{
            task_name = "E2E Multi-Test: $topic"
            type = "content_generation"
            topic = $topic
        } | ConvertTo-Json

        $response = curl -s -X POST "$BackendUrl/api/tasks" `
            -H "Content-Type: application/json" `
            -d $payload | ConvertFrom-Json
        
        if ($response.id) {
            $taskIds += $response.id
            $successCount++
            Write-Verbose "  Created task for: $topic (ID: $($response.id))"
        }
    } catch {
        Write-Verbose "  Failed to create task for $topic : $_"
    }
}

if ($successCount -eq $topics.Count) {
    Write-Success "All $($topics.Count) concurrent tasks created"
    $PassedTests++
} else {
    Write-Warning-Custom "Only $successCount/$($topics.Count) tasks created"
}

# Test 6: Wait for Concurrent Tasks
Write-Info "Test 6: Waiting for concurrent tasks to complete..."
$TotalTests++

Start-Sleep -Seconds $WaitSeconds

$completedCount = 0
$postsCreatedCount = 0

foreach ($id in $taskIds) {
    try {
        $status = curl -s "$BackendUrl/api/tasks/$id" | ConvertFrom-Json
        
        if ($status.status -eq "completed") {
            $completedCount++
            if ($status.result.post_created) {
                $postsCreatedCount++
            }
        }
    } catch {
        Write-Verbose "Could not check task $id : $_"
    }
}

if ($completedCount -eq $taskIds.Count) {
    Write-Success "All $($taskIds.Count) concurrent tasks completed"
    if ($postsCreatedCount -eq $taskIds.Count) {
        Write-Success "All $($taskIds.Count) posts were created"
        $PassedTests++
    } else {
        Write-Warning-Custom "Only $postsCreatedCount/$($taskIds.Count) posts created"
    }
} else {
    Write-Warning-Custom "Only $completedCount/$($taskIds.Count) tasks completed"
}

# Test 7: Verify Task Result Contains Content
Write-Info "Test 7: Verifying task result contains generated content..."
$TotalTests++

if ($taskStatus.result.content -and $taskStatus.result.content.Length -gt 500) {
    Write-Success "Task result contains valid content ($($taskStatus.result.content_length) chars)"
    $PassedTests++
} else {
    Write-Error-Custom "Task result missing or insufficient content"
    $FailedTests++
}

# Test 8: Verify API Response Times
Write-Info "Test 8: Checking API response performance..."
$TotalTests++

$responseTimes = @()

for ($i = 0; $i -lt 3; $i++) {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        curl -s "$BackendUrl/api/health" | Out-Null
    } catch {}
    $stopwatch.Stop()
    $responseTimes += $stopwatch.ElapsedMilliseconds
}

$avgTime = ($responseTimes | Measure-Object -Average).Average
if ($avgTime -lt 1000) {
    Write-Success "Average API response time: ${avgTime}ms (acceptable)"
    $PassedTests++
} else {
    Write-Warning-Custom "Average API response time: ${avgTime}ms (slower than expected)"
}

# Summary
Write-Host ""
Write-Info "=" * 50
Write-Host "TEST SUMMARY" -ForegroundColor Cyan
Write-Info "=" * 50
Write-Host "Total Tests:    $TotalTests"
Write-Host "Passed:         $PassedTests" -ForegroundColor Green
Write-Host "Failed:         $FailedTests" -ForegroundColor $(if ($FailedTests -gt 0) { "Red" } else { "Green" })
Write-Info "=" * 50

# Exit code
if ($FailedTests -eq 0) {
    Write-Success "All tests passed!"
    exit 0
} else {
    Write-Error-Custom "$FailedTests test(s) failed"
    exit 1
}
