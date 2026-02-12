# Phase 4 Endpoint Verification Script
# Comprehensive API endpoint testing for 50+ Phase 4 endpoints

$ErrorActionPreference = "Stop"

Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  Phase 4 Endpoint Verification - Comprehensive Test Suite"
Write-Host "════════════════════════════════════════════════════════════"
Write-Host ""

# Configuration
$BaseURL = "http://localhost:8000"
$MaxTimeout = 5000  # milliseconds

# Color helpers
function Write-Success { Write-Host "✅ $args" -ForegroundColor Green }
function Write-Error { Write-Host "❌ $args" -ForegroundColor Red }
function Write-Warning { Write-Host "⚠️  $args" -ForegroundColor Yellow }
function Write-Info { Write-Host "ℹ️  $args" -ForegroundColor Cyan }

# Test counter
$TotalTests = 0
$PassedTests = 0
$FailedTests = 0
$SkippedTests = 0

# Function to test endpoint
function Test-Endpoint {
    param(
        [string]$Method = "GET",
        [string]$Path,
        [string]$Description,
        [hashtable]$Body = $null,
        [int]$ExpectedStatus = 200
    )
    
    $TotalTests++
    $FullURL = "$BaseURL$Path"
    
    try {
        $request = @{
            Uri = $FullURL
            Method = $Method
            Headers = @{
                "Content-Type" = "application/json"
            }
            TimeoutSec = 10
            SkipHttpErrorCheck = $true
        }
        
        if ($Body) {
            $request.Body = ($Body | ConvertTo-Json)
        }
        
        $response = Invoke-WebRequest @request
        
        if ($response.StatusCode -eq $ExpectedStatus) {
            Write-Success "$Method $Path - $Description"
            $PassedTests++
            return $response.Content
        } else {
            Write-Error "$Method $Path - Expected $ExpectedStatus, got $($response.StatusCode)"
            $FailedTests++
            return $null
        }
    } catch {
        Write-Error "$Method $Path - ERROR: $($_.Exception.Message)"
        $FailedTests++
        return $null
    }
}

# ============================================================================
# SECTION 1: CORE HEALTH & STATUS ENDPOINTS (5 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 1: Core Health & Status Endpoints (5)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "1.1 Health Check"
Test-Endpoint -Method "GET" -Path "/health" -Description "System health status"

Write-Info "1.2 Status"
Test-Endpoint -Method "GET" -Path "/status" -Description "Detailed system status"

Write-Info "1.3 Version"
Test-Endpoint -Method "GET" -Path "/version" -Description "API version"

Write-Info "1.4 Metrics"
Test-Endpoint -Method "GET" -Path "/metrics" -Description "System metrics"

Write-Info "1.5 Ready Check"
Test-Endpoint -Method "GET" -Path "/ready" -Description "Readiness probe"

# ============================================================================
# SECTION 2: AGENT REGISTRY ENDPOINTS (11 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 2: Agent Registry Endpoints (11)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "2.1 List All Agents"
$agentList = Test-Endpoint -Method "GET" -Path "/api/agents/list" -Description "Get all agent names"

Write-Info "2.2 Get Agent Registry"
$agentRegistry = Test-Endpoint -Method "GET" -Path "/api/agents/registry" -Description "Full agent registry with metadata"

Write-Info "2.3 Get Specific Agent (content_service)"
Test-Endpoint -Method "GET" -Path "/api/agents/content_service" -Description "Get content_service metadata"

Write-Info "2.4 Get Agent Phases (content_service)"
Test-Endpoint -Method "GET" -Path "/api/agents/content_service/phases" -Description "Get phases for content_service"

Write-Info "2.5 Get Agent Capabilities (content_service)"
Test-Endpoint -Method "GET" -Path "/api/agents/content_service/capabilities" -Description "Get capabilities for content_service"

Write-Info "2.6 Get Agents by Phase (research)"
Test-Endpoint -Method "GET" -Path "/api/agents/by-phase/research" -Description "Get agents handling research phase"

Write-Info "2.7 Get Agents by Capability (content_generation)"
Test-Endpoint -Method "GET" -Path "/api/agents/by-capability/content_generation" -Description "Get agents with content_generation capability"

Write-Info "2.8 Get Agents by Category (content)"
Test-Endpoint -Method "GET" -Path "/api/agents/by-category/content" -Description "Get all content agents"

Write-Info "2.9 Search Agents (phase=draft&category=content)"
Test-Endpoint -Method "GET" -Path "/api/agents/search?phase=draft&category=content" -Description "Search agents by filters"

Write-Info "2.10 List agents (financial category)"
Test-Endpoint -Method "GET" -Path "/api/agents/by-category/financial" -Description "Get financial agents"

Write-Info "2.11 List agents (compliance category)"
Test-Endpoint -Method "GET" -Path "/api/agents/by-category/compliance" -Description "Get compliance agents"

# ============================================================================
# SECTION 3: SERVICE REGISTRY ENDPOINTS (7 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 3: Service Registry Endpoints (7)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "3.1 Get Service Registry"
Test-Endpoint -Method "GET" -Path "/api/services/registry" -Description "Full service registry schema"

Write-Info "3.2 List Services"
Test-Endpoint -Method "GET" -Path "/api/services/list" -Description "Get all service names"

Write-Info "3.3 Get Service Metadata (content_service)"
Test-Endpoint -Method "GET" -Path "/api/services/content_service" -Description "Get content_service metadata"

Write-Info "3.4 Get Service Actions (content_service)"
Test-Endpoint -Method "GET" -Path "/api/services/content_service/actions" -Description "Get actions for content_service"

Write-Info "3.5 Get Action Details"
Test-Endpoint -Method "GET" -Path "/api/services/content_service/actions/generate_content" -Description "Get specific action details"

Write-Info "3.6 List financial service"
Test-Endpoint -Method "GET" -Path "/api/services/financial_service" -Description "Get financial_service metadata"

Write-Info "3.7 List compliance service"
Test-Endpoint -Method "GET" -Path "/api/services/compliance_service" -Description "Get compliance_service metadata"

# ============================================================================
# SECTION 4: TASK ENDPOINTS (8 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 4: Task Management Endpoints (8)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "4.1 List Tasks"
Test-Endpoint -Method "GET" -Path "/api/tasks" -Description "Get all tasks"

Write-Info "4.2 List Tasks with limit"
Test-Endpoint -Method "GET" -Path "/api/tasks?limit=10" -Description "Get tasks with pagination"

Write-Info "4.3 Create Task"
$taskBody = @{
    title = "Test Task"
    description = "Phase 4 verification task"
    task_type = "content_generation"
}
$newTask = Test-Endpoint -Method "POST" -Path "/api/tasks" -Description "Create new task" -Body $taskBody -ExpectedStatus 201

Write-Info "4.4 Get Task Status"
Test-Endpoint -Method "GET" -Path "/api/tasks?status=pending" -Description "Get pending tasks"

Write-Info "4.5 Get Recent Tasks"
Test-Endpoint -Method "GET" -Path "/api/tasks?status=completed&limit=5" -Description "Get completed tasks"

Write-Info "4.6 Task History"
Test-Endpoint -Method "GET" -Path "/api/tasks/history" -Description "Get task history"

Write-Info "4.7 Task Statistics"
Test-Endpoint -Method "GET" -Path "/api/tasks/stats" -Description "Get task statistics"

Write-Info "4.8 Task Filters (by created_by)"
Test-Endpoint -Method "GET" -Path "/api/tasks?created_by=system" -Description "Filter tasks by creator"

# ============================================================================
# SECTION 5: WORKFLOW ENDPOINTS (5 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 5: Workflow Endpoints (5)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "5.1 List Workflow Templates"
Test-Endpoint -Method "GET" -Path "/api/workflows" -Description "Get available workflow templates"

Write-Info "5.2 Get Workflow Templates"
Test-Endpoint -Method "GET" -Path "/api/workflows/templates" -Description "Get workflow templates"

Write-Info "5.3 Get Workflow History"
Test-Endpoint -Method "GET" -Path "/api/workflows/history" -Description "Get workflow execution history"

Write-Info "5.4 Get Running Workflows"
Test-Endpoint -Method "GET" -Path "/api/workflows/running" -Description "Get active workflows"

Write-Info "5.5 Workflow Statistics"
Test-Endpoint -Method "GET" -Path "/api/workflows/stats" -Description "Get workflow statistics"

# ============================================================================
# SECTION 6: MODEL & LLM ENDPOINTS (6 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 6: Model & LLM Endpoints (6)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "6.1 List Available Models"
Test-Endpoint -Method "GET" -Path "/api/models" -Description "Get available LLM models"

Write-Info "6.2 Get Model Health"
Test-Endpoint -Method "GET" -Path "/api/models/health" -Description "Check model provider health"

Write-Info "6.3 Ollama Models"
Test-Endpoint -Method "GET" -Path "/api/models/ollama" -Description "Get Ollama models"

Write-Info "6.4 OpenAI Models"
Test-Endpoint -Method "GET" -Path "/api/models/openai" -Description "Get OpenAI models"

Write-Info "6.5 Model Configuration"
Test-Endpoint -Method "GET" -Path "/api/models/config" -Description "Get model configuration"

Write-Info "6.6 Model Routing Info"
Test-Endpoint -Method "GET" -Path "/api/models/routing" -Description "Get model routing configuration"

# ============================================================================
# SECTION 7: ANALYTICS & MONITORING (8 endpoints)
# ============================================================================

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "SECTION 7: Analytics & Monitoring (8)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""

Write-Info "7.1 System Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics" -Description "Get system analytics"

Write-Info "7.2 Agent Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/agents" -Description "Get agent analytics"

Write-Info "7.3 Content Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/content" -Description "Get content analytics"

Write-Info "7.4 Cost Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/costs" -Description "Get cost tracking data"

Write-Info "7.5 Performance Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/performance" -Description "Get performance metrics"

Write-Info "7.6 Usage Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/usage" -Description "Get usage statistics"

Write-Info "7.7 Error Analytics"
Test-Endpoint -Method "GET" -Path "/api/analytics/errors" -Description "Get error tracking"

Write-Info "7.8 Dashboard Data"
Test-Endpoint -Method "GET" -Path "/api/analytics/dashboard" -Description "Get dashboard data"

# ============================================================================
# SUMMARY
# ============================================================================

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  TEST SUMMARY"
Write-Host "════════════════════════════════════════════════════════════"
Write-Host ""

$SuccessRate = if ($TotalTests -gt 0) { [math]::Round(($PassedTests / $TotalTests) * 100, 2) } else { 0 }

Write-Host "Total Tests:    $TotalTests"
Write-Host "Passed:         $PassedTests ($(Write-Success -NoNewline))"
Write-Host "Failed:         $FailedTests ($(Write-Error -NoNewline))"
Write-Host "Success Rate:   $SuccessRate%"
Write-Host ""

if ($PassedTests -eq $TotalTests) {
    Write-Success "All Phase 4 endpoints are operational! ✨"
    exit 0
} else {
    Write-Warning "Some endpoints failed - review output above"
    exit 1
}
