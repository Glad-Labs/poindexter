<#
.SYNOPSIS
    Start the local worker — connects to production DB, runs content pipeline on local hardware.

.DESCRIPTION
    Runs the FastAPI backend in worker mode. The worker:
    - Connects to the production Railway PostgreSQL
    - Registers itself with the coordinator
    - Claims content tasks from the queue
    - Runs the 6-stage pipeline using local Ollama (5090 GPU) or cloud API keys
    - Writes results back to the production DB

    API keys are read from ~/.openclaw/workspace/.env (Anthropic, OpenAI, etc.)
    The coordinator on Railway handles API requests, the worker handles compute.

.EXAMPLE
    .\scripts\start-worker.ps1
    .\scripts\start-worker.ps1 -Port 8001
#>

param(
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendDir = Join-Path $ProjectRoot "src\cofounder_agent"
$OpenClawEnv = "$env:USERPROFILE\.openclaw\workspace\.env"

# Load secrets from OpenClaw workspace .env
if (Test-Path $OpenClawEnv) {
    Write-Host "Loading API keys from $OpenClawEnv" -ForegroundColor Cyan
    Get-Content $OpenClawEnv | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                $key = $parts[0].Trim()
                $val = $parts[1].Trim()
                # Only set if not already in environment
                if (-not [Environment]::GetEnvironmentVariable($key)) {
                    [Environment]::SetEnvironmentVariable($key, $val, "Process")
                }
            }
        }
    }
} else {
    Write-Warning "No .env found at $OpenClawEnv — worker will have no API keys"
}

# Get production DATABASE_URL from Railway (public proxy, not internal)
Write-Host "Fetching production DATABASE_URL from Railway..." -ForegroundColor Cyan
# Railway removed: $railwayOutput = & railway variables --service Postgres --json 2>&1
try {
    $railwayJson = $railwayOutput | ConvertFrom-Json
    $publicUrl = $railwayJson.DATABASE_PUBLIC_URL
    if ($publicUrl) {
        $env:DATABASE_URL = $publicUrl
        Write-Host "Connected to production database (public proxy)" -ForegroundColor Green
    } else {
        throw "No DATABASE_PUBLIC_URL found"
    }
} catch {
    Write-Warning "Could not fetch DATABASE_PUBLIC_URL from Railway: $_"
    Write-Warning "Falling back to local DB."
    if (-not $env:DATABASE_URL) {
        $env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
    }
}

# Set worker mode
$env:DEPLOYMENT_MODE = "worker"
$env:ENVIRONMENT = "production"

# Local brain DB for tasks, embeddings, audit logging (pgvector)
$env:LOCAL_DATABASE_URL = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
Write-Host "Local brain DB: localhost:5433/gladlabs_brain" -ForegroundColor Cyan
$env:PORT = $Port
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"

Write-Host ""
Write-Host "=== Glad Labs Worker ===" -ForegroundColor Cyan
Write-Host "Mode:     worker (heavy compute)" -ForegroundColor White
Write-Host "Port:     $Port" -ForegroundColor White
Write-Host "Ollama:   $env:OLLAMA_BASE_URL" -ForegroundColor White
Write-Host "Database: production (Railway)" -ForegroundColor White
Write-Host "========================" -ForegroundColor Cyan
Write-Host ""

# Start the worker
Set-Location $BackendDir
poetry run uvicorn main:app --host 0.0.0.0 --port $Port
