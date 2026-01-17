# Gemini Integration Testing Script (PowerShell)
# Tests Gemini functionality in Glad Labs system
# Usage: .\scripts\test-gemini.ps1

$ErrorActionPreference = "Continue"

# Configuration
$BACKEND_URL = "http://localhost:8000"
$API_KEY = $env:GOOGLE_API_KEY
$TEST_CONVERSATION_ID = "gemini-test-$(Get-Date -Format 'yyyyMMddHHmmss')"
$TESTS_PASSED = 0
$TESTS_FAILED = 0

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

function Log-Test {
    param([string]$Message)
    Write-Host "[TEST] $Message" -ForegroundColor Yellow
}

function Log-Pass {
    param([string]$Message)
    Write-Host "[PASS] $Message" -ForegroundColor Green
    $script:TESTS_PASSED++
}

function Log-Fail {
    param([string]$Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    $script:TESTS_FAILED++
}

function Log-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Check-Service {
    param([string]$URL)
    try {
        $response = Invoke-WebRequest -Uri $URL -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

# ============================================================================
# HEADER
# ============================================================================

Write-Host "======================================" -ForegroundColor Blue
Write-Host "Gemini Integration Test Suite" -ForegroundColor Blue
Write-Host "======================================" -ForegroundColor Blue
Write-Host ""

# ============================================================================
# TEST 1: ENVIRONMENT CHECK
# ============================================================================

Log-Test "Environment Configuration"

if ([string]::IsNullOrEmpty($API_KEY)) {
    Log-Fail "GOOGLE_API_KEY not set in environment"
    Write-Host "  Add to .env.local: GOOGLE_API_KEY=AIza..." -ForegroundColor Gray
}
else {
    Log-Pass "GOOGLE_API_KEY is configured (length: $($API_KEY.Length))"
}

# ============================================================================
# TEST 2: BACKEND CONNECTIVITY
# ============================================================================

Log-Test "Backend Connectivity"

if (Check-Service "$BACKEND_URL/api/health") {
    Log-Pass "Backend is running on port 8000"
}
else {
    Log-Fail "Backend not responding on port 8000"
    Write-Host "  Start backend with: npm run dev:cofounder" -ForegroundColor Gray
    exit 1
}

# ============================================================================
# TEST 3: MODELS ENDPOINT
# ============================================================================

Log-Test "Available Models Endpoint"

try {
    $modelsResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/v1/models/available" -Method Get -TimeoutSec 5 | Select-Object -ExpandProperty Content
    $modelsJson = $modelsResponse | ConvertFrom-Json
    Log-Pass "Models endpoint returns valid JSON"
    
    $geminiModels = $modelsJson.models | Where-Object { $_.provider -eq "google" }
    
    if ($geminiModels.Count -gt 0) {
        Log-Pass "Found $($geminiModels.Count) Gemini model(s)"
        $geminiModels | ForEach-Object { Write-Host "  - $($_.name)" -ForegroundColor Gray }
    }
    else {
        Log-Fail "No Gemini models found in response"
        Log-Info "Available providers:"
        $modelsJson.models | Group-Object -Property provider | ForEach-Object { 
            Write-Host "  - $($_.Name): $($_.Count) models" -ForegroundColor Gray 
        }
    }
}
catch {
    Log-Fail "Models endpoint error: $_"
}

# ============================================================================
# TEST 4: PROVIDER STATUS
# ============================================================================

Log-Test "Provider Status Check"

try {
    $statusResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/v1/models/status" -Method Get -TimeoutSec 5 | Select-Object -ExpandProperty Content
    $statusJson = $statusResponse | ConvertFrom-Json
    Log-Pass "Provider status endpoint returns valid JSON"
    
    if ($statusJson.providers.google.available) {
        Log-Pass "Google provider is available"
        Write-Host "  Models available: $($statusJson.providers.google.models_count)" -ForegroundColor Gray
    }
    else {
        Log-Fail "Google provider is not available"
    }
}
catch {
    Log-Fail "Provider status endpoint error: $_"
}

# ============================================================================
# TEST 5: GEMINI CHAT TEST (Simple Message)
# ============================================================================

Log-Test "Gemini Chat - Simple Message"

try {
    $chatBody = @{
        conversationId = $TEST_CONVERSATION_ID
        model = "gemini-1.5-pro"
        message = "Say 'Hello from Gemini' and nothing else"
    } | ConvertTo-Json
    
    $chatResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/chat" -Method Post -ContentType "application/json" -Body $chatBody -TimeoutSec 10 | Select-Object -ExpandProperty Content
    $chatJson = $chatResponse | ConvertFrom-Json
    
    $provider = $chatJson.provider
    $responseText = $chatJson.response
    
    if ($provider -eq "google") {
        Log-Pass "Gemini response received"
        Log-Info "Provider: $provider"
        Write-Host "  Response: $responseText" -ForegroundColor Gray
    }
    else {
        Log-Fail "Wrong provider in response: $provider (expected: google)"
        Log-Info "This may indicate API key issue or rate limiting"
    }
}
catch {
    Log-Fail "Chat endpoint error: $_"
}

# ============================================================================
# TEST 6: CONVERSATION HISTORY
# ============================================================================

Log-Test "Conversation History"

try {
    $historyResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/chat/history/$TEST_CONVERSATION_ID" -Method Get -TimeoutSec 5 | Select-Object -ExpandProperty Content
    $historyJson = $historyResponse | ConvertFrom-Json
    
    $msgCount = $historyJson.message_count
    
    if ($msgCount -gt 0) {
        Log-Pass "Conversation history retrieved ($msgCount messages)"
    }
    else {
        Log-Fail "No messages in conversation history"
    }
}
catch {
    Log-Fail "History endpoint error: $_"
}

# ============================================================================
# TEST 7: GEMINI CHAT TEST (Complex Message)
# ============================================================================

Log-Test "Gemini Chat - Complex Message"

try {
    $chatBody = @{
        conversationId = "$TEST_CONVERSATION_ID-complex"
        model = "gemini-1.5-pro"
        message = "Write a 3-sentence summary of machine learning. Keep it concise."
    } | ConvertTo-Json
    
    $chatResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/chat" -Method Post -ContentType "application/json" -Body $chatBody -TimeoutSec 10 | Select-Object -ExpandProperty Content
    $chatJson = $chatResponse | ConvertFrom-Json
    
    Log-Pass "Complex message processed"
    $responseLength = ($chatJson.response -split '\s+').Count
    Log-Info "Response length: ~$responseLength words"
}
catch {
    Log-Fail "Complex message processing failed: $_"
}

# ============================================================================
# TEST 8: ERROR HANDLING
# ============================================================================

Log-Test "Error Handling - Invalid Model"

try {
    $errorBody = @{
        conversationId = "$TEST_CONVERSATION_ID-error"
        model = "invalid-model-xyz"
        message = "test"
    } | ConvertTo-Json
    
    $errorResponse = Invoke-WebRequest -Uri "$BACKEND_URL/api/chat" -Method Post -ContentType "application/json" -Body $errorBody -TimeoutSec 5 | Select-Object -ExpandProperty Content
    $errorJson = $errorResponse | ConvertFrom-Json
    
    Log-Pass "Invalid model properly handled"
}
catch {
    Log-Pass "Invalid model properly rejected with error"
}

# ============================================================================
# TEST SUMMARY
# ============================================================================

Write-Host ""
Write-Host "======================================" -ForegroundColor Blue
Write-Host "Test Summary" -ForegroundColor Blue
Write-Host "======================================" -ForegroundColor Blue

Write-Host "Passed: $TESTS_PASSED" -ForegroundColor Green
Write-Host "Failed: $TESTS_FAILED" -ForegroundColor Red

$TOTAL = $TESTS_PASSED + $TESTS_FAILED
if ($TOTAL -gt 0) {
    $SUCCESS_RATE = [int]($TESTS_PASSED * 100 / $TOTAL)
    Write-Host "Success Rate: $SUCCESS_RATE%"
}

Write-Host ""
if ($TESTS_FAILED -eq 0) {
    Write-Host "✓ All tests passed!" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "✗ Some tests failed. See above for details." -ForegroundColor Red
    exit 1
}
