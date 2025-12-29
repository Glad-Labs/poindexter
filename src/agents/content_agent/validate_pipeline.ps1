# Content Pipeline Pre-Flight Validation Script (PowerShell)
# Run this before executing the content agent pipeline

$ErrorActionPreference = "Continue"

Write-Host "🔍 Glad Labs Content Pipeline Pre-Flight Check" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

$Failures = 0

# Function to check environment variable
function Check-EnvVar {
    param(
        [string]$VarName,
        [bool]$Required = $true
    )
    
    $value = [Environment]::GetEnvironmentVariable($VarName)
    if ([string]::IsNullOrEmpty($value)) {
        if ($Required) {
            Write-Host "✗ ${VarName}: NOT SET (REQUIRED)" -ForegroundColor Red
            $script:Failures++
        } else {
            Write-Host "⚠ ${VarName}: NOT SET (OPTIONAL)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "✓ ${VarName}: SET" -ForegroundColor Green
    }
}

# 1. Environment Variables Check
Write-Host "`n1️⃣  Checking environment variables..." -ForegroundColor Green
Write-Host "----------------------------------------"

# Critical variables
Check-EnvVar "STRAPI_API_URL"
Check-EnvVar "STRAPI_API_TOKEN"
Check-EnvVar "FIRESTORE_PROJECT_ID"
Check-EnvVar "GCS_BUCKET_NAME"
Check-EnvVar "PUBSUB_TOPIC"
Check-EnvVar "PUBSUB_SUBSCRIPTION"

# API Keys (at least one should be set)
Write-Host "`nAI Provider API Keys (at least one required):" -ForegroundColor Green
$HasAIKey = $false
if (-not [string]::IsNullOrEmpty($env:OPENAI_API_KEY)) {
    Write-Host "✓ OPENAI_API_KEY: SET" -ForegroundColor Green
    $HasAIKey = $true
}
if (-not [string]::IsNullOrEmpty($env:ANTHROPIC_API_KEY)) {
    Write-Host "✓ ANTHROPIC_API_KEY: SET" -ForegroundColor Green
    $HasAIKey = $true
}
if (-not [string]::IsNullOrEmpty($env:GOOGLE_API_KEY)) {
    Write-Host "✓ GOOGLE_API_KEY: SET" -ForegroundColor Green
    $HasAIKey = $true
}
if (-not $HasAIKey) {
    Write-Host "✗ No AI provider API key found (need at least one)" -ForegroundColor Red
    $Failures++
}

Check-EnvVar "PEXELS_API_KEY" -Required $false
Check-EnvVar "SERPER_API_KEY" -Required $false

# 2. Strapi Connectivity Check
Write-Host "`n2️⃣  Checking Strapi connectivity..." -ForegroundColor Green
Write-Host "----------------------------------------"

if (-not [string]::IsNullOrEmpty($env:STRAPI_API_URL) -and -not [string]::IsNullOrEmpty($env:STRAPI_API_TOKEN)) {
    try {
        $headers = @{
            "Authorization" = "Bearer $env:STRAPI_API_TOKEN"
        }
        $response = Invoke-WebRequest -Uri "$env:STRAPI_API_URL/api/posts?pagination[limit]=1" -Headers $headers -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        Write-Host "✓ Strapi API accessible at $env:STRAPI_API_URL" -ForegroundColor Green
    } catch {
        Write-Host "✗ Cannot connect to Strapi at $env:STRAPI_API_URL" -ForegroundColor Red
        Write-Host "  Make sure Strapi is running: cd cms/strapi-main; npm run develop" -ForegroundColor Yellow
        $Failures++
    }
} else {
    Write-Host "⚠ Skipping Strapi check (missing URL or token)" -ForegroundColor Yellow
}

# 3. Python Environment Check
Write-Host "`n3️⃣  Checking Python environment..." -ForegroundColor Green
Write-Host "----------------------------------------"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Python found: $pythonVersion" -ForegroundColor Green
    
    if (Test-Path "requirements.txt") {
        Write-Host "  Installing dependencies..."
        pip install -q -r requirements.txt 2>&1 | Out-Null
        Write-Host "✓ Dependencies installed" -ForegroundColor Green
    } else {
        Write-Host "⚠ requirements.txt not found (run from content agent directory)" -ForegroundColor Yellow
    }
} else {
    Write-Host "✗ Python not found in PATH" -ForegroundColor Red
    $Failures++
}

# 4. Module Import Check
Write-Host "`n4️⃣  Checking Python modules..." -ForegroundColor Green
Write-Host "----------------------------------------"

if ($pythonCmd) {
    $importTest = python -c "from orchestrator import Orchestrator" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Orchestrator module imports successfully" -ForegroundColor Green
    } else {
        Write-Host "✗ Cannot import Orchestrator module" -ForegroundColor Red
        Write-Host "  Run from: src/agents/content_agent/" -ForegroundColor Yellow
        $Failures++
    }
}

# 5. Directory Structure Check
Write-Host "`n5️⃣  Checking directory structure..." -ForegroundColor Green
Write-Host "----------------------------------------"

function Check-Directory {
    param([string]$Path)
    if (Test-Path $Path -PathType Container) {
        Write-Host "✓ $Path exists" -ForegroundColor Green
    } else {
        Write-Host "✗ $Path not found" -ForegroundColor Red
        $script:Failures++
    }
}

function Check-File {
    param([string]$Path)
    if (Test-Path $Path -PathType Leaf) {
        Write-Host "✓ $Path exists" -ForegroundColor Green
    } else {
        Write-Host "⚠ $Path not found" -ForegroundColor Yellow
    }
}

Check-Directory "agents"
Check-Directory "services"
Check-Directory "utils"
Check-Directory "tests"
Check-File "orchestrator.py"
Check-File "config.py"
Check-File "prompts.json"

# 6. Quick Smoke Test
Write-Host "`n6️⃣  Running smoke tests..." -ForegroundColor Green
Write-Host "----------------------------------------"

$pytestCmd = Get-Command pytest -ErrorAction SilentlyContinue
if ($pytestCmd -and (Test-Path "tests")) {
    Write-Host "Running orchestrator initialization test..."
    Push-Location tests
    $testOutput = python -m pytest test_orchestrator_init.py::test_orchestrator_initializes -v 2>&1 | Select-Object -Last 20
    Pop-Location
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Smoke test passed" -ForegroundColor Green
    } else {
        Write-Host "✗ Smoke test failed" -ForegroundColor Red
        Write-Host $testOutput
        $Failures++
    }
} else {
    Write-Host "⚠ pytest not available or tests directory not found" -ForegroundColor Yellow
    Write-Host "  Install with: pip install pytest" -ForegroundColor Yellow
}

# Final Summary
Write-Host "`n=============================================="
if ($Failures -eq 0) {
    Write-Host "✅ ALL CHECKS PASSED" -ForegroundColor Green
    Write-Host ""
    Write-Host "Content pipeline is ready to run!"
    Write-Host ""
    Write-Host "To start the pipeline:"
    Write-Host "  cd src/agents/content_agent"
    Write-Host "  python orchestrator.py"
    Write-Host ""
    Write-Host "To run with specific topic:"
    Write-Host "  python create_task.py --topic 'Your Topic Here'"
    exit 0
} else {
    Write-Host "❌ $Failures CHECK(S) FAILED" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please fix the issues above before running the pipeline."
    Write-Host ""
    Write-Host "Common fixes:"
    Write-Host "  - Set missing environment variables in .env file"
    Write-Host "  - Start Strapi: cd cms/strapi-main; npm run develop"
    Write-Host "  - Install Python deps: pip install -r requirements.txt"
    Write-Host "  - Run from correct directory: cd src/agents/content_agent"
    exit 1
}
