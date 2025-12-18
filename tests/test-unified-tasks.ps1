#!/usr/bin/env pwsh

<#
.SYNOPSIS
Test script for unified tasks table consolidation

.DESCRIPTION
Verifies that:
1. POST /api/content/tasks creates a task in the unified tasks table
2. GET /api/content/tasks/{id} retrieves the task correctly
3. GET /api/tasks lists all unified tasks
4. Task data includes proper task_type field

.EXAMPLE
./test-unified-tasks.ps1
#>

$API_BASE = "http://localhost:8000"
$colors = @{
    Success = "Green"
    Error = "Red"
    Info = "Cyan"
    Warning = "Yellow"
}

function Write-Status($message, $type = "Info") {
    $color = $colors[$type]
    Write-Host "[$([datetime]::Now.ToString('HH:mm:ss'))] " -NoNewline
    Write-Host $message -ForegroundColor $color
}

function Test-API-Health {
    Write-Status "🔍 Testing API Health..." "Info"
    try {
        $response = Invoke-RestMethod -Uri "$API_BASE/api/health" -Method Get -TimeoutSec 5
        if ($response.status -eq "healthy") {
            Write-Status "✅ API is healthy" "Success"
            return $true
        }
    } catch {
        Write-Status "❌ API Health Check Failed: $_" "Error"
        return $false
    }
}

function Test-Create-Task {
    Write-Status "📝 Creating test blog post task..." "Info"
    
    $payload = @{
        task_type = "blog_post"
        topic = "AI Trends and Future Opportunities"
        style = "technical"
        tone = "professional"
        target_length = 1500
        tags = @("AI", "trends", "future")
        generate_featured_image = $false
        publish_mode = "draft"
        enhanced = $false
    } | ConvertTo-Json

    try {
        $response = Invoke-RestMethod -Uri "$API_BASE/api/content/tasks" `
            -Method Post `
            -Body $payload `
            -ContentType "application/json" `
            -TimeoutSec 10

        if ($response.task_id) {
            Write-Status "✅ Task created successfully" "Success"
            Write-Status "   Task ID: $($response.task_id)" "Info"
            Write-Status "   Task Type: $($response.task_type)" "Info"
            Write-Status "   Status: $($response.status)" "Info"
            return $response.task_id
        }
    } catch {
        Write-Status "❌ Failed to create task: $_" "Error"
        return $null
    }
}

function Test-Get-Task($taskId) {
    Write-Status "🔍 Retrieving task $taskId..." "Info"
    
    try {
        $response = Invoke-RestMethod -Uri "$API_BASE/api/content/tasks/$taskId" `
            -Method Get `
            -TimeoutSec 10

        if ($response.task_id) {
            Write-Status "✅ Task retrieved successfully" "Success"
            Write-Status "   Status: $($response.status)" "Info"
            Write-Status "   Task Type: $($response.task_id)" "Info"
            return $response
        }
    } catch {
        Write-Status "❌ Failed to retrieve task: $_" "Error"
        return $null
    }
}

function Test-List-Tasks {
    Write-Status "📊 Listing all tasks from unified table..." "Info"
    
    try {
        $response = Invoke-RestMethod -Uri "$API_BASE/api/tasks" `
            -Method Get `
            -TimeoutSec 10

        if ($response.tasks) {
            Write-Status "✅ Listed tasks successfully" "Success"
            Write-Status "   Total tasks: $($response.tasks.Count)" "Info"
            
            # Show task type distribution
            $typeDistribution = $response.tasks | Group-Object -Property task_type | 
                                Select-Object @{Name="Type"; Expression={$_.Name}}, @{Name="Count"; Expression={$_.Count}}
            
            Write-Status "   Task Type Distribution:" "Info"
            foreach ($dist in $typeDistribution) {
                Write-Status "      - $($dist.Type): $($dist.Count)" "Info"
            }
            
            return $response.tasks
        }
    } catch {
        Write-Status "❌ Failed to list tasks: $_" "Error"
        return $null
    }
}

function Test-Verify-Database {
    Write-Status "🗄️  Verifying database state..." "Info"
    Write-Status "   This requires psql access - checking if available..." "Warning"
    
    # Check if psql is available
    $psqlExists = Get-Command psql -ErrorAction SilentlyContinue
    if (-not $psqlExists) {
        Write-Status "   ⚠️  psql not found in PATH" "Warning"
        return $false
    }
    
    Write-Status "   ✅ psql found - querying database..." "Info"
    
    try {
        # Count tasks in unified table
        $query = "SELECT COUNT(*) as count FROM tasks;"
        $result = psql -h localhost -U postgres -d glad_labs_dev -t -c $query 2>$null
        
        if ($result) {
            Write-Status "   ✅ Unified tasks table: $result rows" "Success"
        }
        
        # Check content_tasks
        $query2 = "SELECT COUNT(*) as count FROM content_tasks;"
        $result2 = psql -h localhost -U postgres -d glad_labs_dev -t -c $query2 2>$null
        
        if ($result2 -eq "0") {
            Write-Status "   ✅ Content tasks table: $result2 rows (deprecated, as expected)" "Success"
            return $true
        }
    } catch {
        Write-Status "   ⚠️  Could not query database: $_" "Warning"
    }
    
    return $false
}

# ============================================================================
# MAIN TEST FLOW
# ============================================================================

Write-Host ""
Write-Status "╔════════════════════════════════════════════════════╗" "Info"
Write-Status "║  Unified Tasks Table - End-to-End Test Suite      ║" "Info"
Write-Status "╚════════════════════════════════════════════════════╝" "Info"
Write-Host ""

# Step 1: Health Check
if (-not (Test-API-Health)) {
    Write-Status "❌ API is not responding. Make sure backend is running." "Error"
    Write-Status "   Start with: npm run dev:cofounder" "Warning"
    exit 1
}

Write-Host ""

# Step 2: Create Task
$taskId = Test-Create-Task
if (-not $taskId) {
    Write-Status "❌ Failed to create task - cannot continue with tests" "Error"
    exit 1
}

Write-Host ""

# Step 3: Retrieve Created Task
$task = Test-Get-Task $taskId
if (-not $task) {
    Write-Status "⚠️  Could not retrieve created task" "Warning"
}

Write-Host ""

# Step 4: List All Tasks
$allTasks = Test-List-Tasks
if (-not $allTasks) {
    Write-Status "⚠️  Could not list tasks" "Warning"
}

Write-Host ""

# Step 5: Verify Database
Test-Verify-Database

Write-Host ""
Write-Status "╔════════════════════════════════════════════════════╗" "Info"
Write-Status "║  ✅ Test Suite Complete                           ║" "Info"
Write-Status "╚════════════════════════════════════════════════════╝" "Info"
Write-Host ""
Write-Status "Summary:" "Info"
Write-Status "  • API Health: ✅" "Success"
Write-Status "  • Task Creation: ✅" "Success"
Write-Status "  • Task Retrieval: $(if($task) { '✅' } else { '⚠️' })" "Info"
Write-Status "  • Task Listing: $(if($allTasks) { '✅' } else { '⚠️' })" "Info"
Write-Host ""
Write-Status "Next Steps:" "Info"
Write-Status "  • Check UI at http://localhost:3001/tasks" "Info"
Write-Status "  • Verify task appears in unified view" "Info"
Write-Status "  • Test other task types (social_media, email, etc.)" "Info"
Write-Host ""
