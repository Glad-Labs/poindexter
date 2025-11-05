# Verify End-to-End Pipeline
# This script checks the entire Glad Labs content creation pipeline

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Glad Labs Pipeline Verification" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ErrorActionPreference = "Continue"
$failures = 0

# Step 1: Check Services
Write-Host "Step 1: Checking services..." -ForegroundColor Yellow
npm run services:check
if ($LASTEXITCODE -ne 0) { $failures++ }
Write-Host ""

# Step 2: Run Python Smoke Tests
Write-Host "Step 2: Running Python smoke tests..." -ForegroundColor Yellow
npm run test:python:smoke
if ($LASTEXITCODE -ne 0) { $failures++ }
Write-Host ""

# Step 3: Run Frontend Tests (CI mode)
Write-Host "Step 3: Running Frontend tests (CI mode)..." -ForegroundColor Yellow
npm run test:frontend:ci
if ($LASTEXITCODE -ne 0) { $failures++ }
Write-Host ""

# Step 4: Check Builds
Write-Host "Step 4: Checking builds..." -ForegroundColor Yellow

Write-Host "  Building Public Site..." -ForegroundColor Gray
npm run build --workspace=web/public-site
if ($LASTEXITCODE -ne 0) { 
    Write-Host "  Public Site build FAILED" -ForegroundColor Red
    $failures++ 
} else {
    Write-Host "  Public Site build OK" -ForegroundColor Green
}

Write-Host "  Building Oversight Hub..." -ForegroundColor Gray
npm run build --workspace=web/oversight-hub
if ($LASTEXITCODE -ne 0) { 
    Write-Host "  Oversight Hub build FAILED" -ForegroundColor Red
    $failures++ 
} else {
    Write-Host "  Oversight Hub build OK" -ForegroundColor Green
}

Write-Host ""

# Summary
Write-Host "========================================" -ForegroundColor Cyan
if ($failures -eq 0) {
    Write-Host " Pipeline verification PASSED" -ForegroundColor Green
    Write-Host " All checks completed successfully!" -ForegroundColor Green
} else {
    Write-Host " Pipeline verification FAILED" -ForegroundColor Red
    Write-Host " $failures check(s) failed" -ForegroundColor Red
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

exit $failures

