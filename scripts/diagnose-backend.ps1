param()

# Simple diagnostic script - no fancy characters
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
<<<<<<< HEAD
Write-Host "Glad Labs - BACKEND DIAGNOSTICS" -ForegroundColor Cyan
=======
Write-Host "Glad LABS - BACKEND DIAGNOSTICS" -ForegroundColor Cyan
>>>>>>> feat/refine
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Python
Write-Host "[1] Python Installation" -ForegroundColor Cyan
try {
    $pyVer = python --version 2>&1
    Write-Host "  OK - $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  ERROR - Python not found" -ForegroundColor Red
}

# 2. Required modules
Write-Host "`n[2] Python Modules" -ForegroundColor Cyan
foreach ($module in @("fastapi", "uvicorn", "pydantic", "aiohttp", "requests")) {
    $result = python -c "import $module" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK - $module" -ForegroundColor Green
    } else {
        Write-Host "  ERROR - $module not installed" -ForegroundColor Red
    }
}

# 3. Environment variables
Write-Host "`n[3] Environment Variables" -ForegroundColor Cyan
foreach ($var in @("STRAPI_API_URL", "STRAPI_API_TOKEN", "PEXELS_API_KEY")) {
    $val = (Get-Item -Path env:$var -ErrorAction SilentlyContinue).Value
    if ($val) {
        Write-Host "  OK - $var = $(($val.Substring(0, [Math]::Min(10, $val.Length))) + '...')" -ForegroundColor Green
    } else {
        Write-Host "  WARN - $var not set" -ForegroundColor Yellow
    }
}

# 4. Ollama
Write-Host "`n[4] Ollama (Local AI)" -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "  OK - Ollama is running" -ForegroundColor Green
        $models = ($response.Content | ConvertFrom-Json).models
        if ($models) {
            foreach ($m in $models) {
                Write-Host "    - $($m.name)" -ForegroundColor Gray
            }
        }
    }
} catch {
    Write-Host "  WARN - Ollama not responding (start with 'ollama serve')" -ForegroundColor Yellow
}

# 5. FastAPI Server
Write-Host "`n[5] FastAPI Backend" -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -TimeoutSec 3 -ErrorAction Stop
    Write-Host "  OK - Server running on port 8000" -ForegroundColor Green
} catch {
    Write-Host "  ERROR - Server not responding on http://localhost:8000" -ForegroundColor Red
    Write-Host "    Start with: cd src/cofounder_agent && python -m uvicorn main:app --reload" -ForegroundColor Gray
}

# 6. API Endpoints
Write-Host "`n[6] API Endpoints" -ForegroundColor Cyan
$endpoints = @(
    @{ url = "http://localhost:8000/api/health"; name = "Health check" },
    @{ url = "http://localhost:8000/api/content/drafts"; name = "List drafts" },
    @{ url = "http://localhost:8000/docs"; name = "API documentation" }
)
foreach ($ep in $endpoints) {
    try {
        $response = Invoke-WebRequest -Uri $ep.url -TimeoutSec 2 -ErrorAction Stop
        Write-Host "  OK - $($ep.name)" -ForegroundColor Green
    } catch {
        Write-Host "  WARN - $($ep.name)" -ForegroundColor Yellow
    }
}

Write-Host "`n=========================================" -ForegroundColor Cyan
Write-Host "Next: Start backend server if not running" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

