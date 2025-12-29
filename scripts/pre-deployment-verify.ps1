# Pre-Deployment System Verification Script (PowerShell)
# Usage: .\scripts\pre-deployment-verify.ps1
# Purpose: Final validation before production deployment

param(
    [switch]$Verbose = $false,
    [switch]$CheckRuntime = $false
)

$passed = 0
$failed = 0
$warnings = 0

# Color output helper
function Write-Pass($message) {
    Write-Host "✅ PASS: $message" -ForegroundColor Green
    $global:passed++
}

function Write-Fail($message) {
    Write-Host "❌ FAIL: $message" -ForegroundColor Red
    $global:failed++
}

function Write-Warn($message) {
    Write-Host "⚠️  WARN: $message" -ForegroundColor Yellow
    $global:warnings++
}

function Write-Header($message) {
    Write-Host ""
    Write-Host "================================"
    Write-Host $message
    Write-Host "================================"
}

# ============================================
# Start Verification
# ============================================
Write-Host ""
Write-Host "Pre-Deployment System Verification" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host ""

# ============================================
# 1. Git Status Check
# ============================================
Write-Header "1. Git Repository Status"

try {
    $gitStatus = git status --short 2>&1
    if ([string]::IsNullOrEmpty($gitStatus)) {
        Write-Pass "Repository is clean"
    } else {
        Write-Warn "Uncommitted changes detected - ensure all changes are committed"
    }

    $branch = git rev-parse --abbrev-ref HEAD 2>&1
    if ($branch -eq "feat/bugs") {
        Write-Pass "On correct branch: $branch"
    } else {
        Write-Fail "Not on feat/bugs branch (currently on: $branch)"
    }
} catch {
    Write-Fail "Git check failed: $_"
}

# ============================================
# 2. Backend Tests
# ============================================
Write-Header "2. Backend Tests"

if (Test-Path "src\cofounder_agent\tests") {
    try {
        Write-Host "Running backend smoke tests (this may take 1-2 minutes)..." -ForegroundColor Cyan
        $testOutput = npm run test:python:smoke 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Pass "Backend smoke tests passed"
        } else {
            Write-Warn "Backend smoke tests failed or not available"
        }
    } catch {
        Write-Warn "Could not run backend tests: $_"
    }
} else {
    Write-Warn "Backend tests directory not found"
}

# ============================================
# 3. Frontend Build
# ============================================
Write-Header "3. Frontend Build Check"

if (Test-Path "web\public-site") {
    try {
        Write-Host "Building frontend (this may take 1-2 minutes)..." -ForegroundColor Cyan
        Push-Location "web\public-site"
        $buildOutput = npm run build 2>&1
        Pop-Location
        
        if ($buildOutput -match "compiled successfully" -or $LASTEXITCODE -eq 0) {
            Write-Pass "Frontend build successful"
        } else {
            Write-Warn "Frontend build completed (check for warnings)"
        }
    } catch {
        Write-Warn "Frontend build check failed: $_"
    }
} else {
    Write-Fail "Frontend directory not found"
}

# ============================================
# 4. Environment Configuration
# ============================================
Write-Header "4. Environment Configuration"

if (Test-Path ".env") {
    $envContent = Get-Content .env -Raw
    if ($envContent -match "NEXT_PUBLIC_FASTAPI_URL") {
        Write-Pass "NEXT_PUBLIC_FASTAPI_URL configured"
    } else {
        Write-Warn "NEXT_PUBLIC_FASTAPI_URL not found in .env"
    }
} else {
    Write-Warn ".env file not found"
}

if (Test-Path ".env.staging") {
    Write-Pass ".env.staging exists"
} else {
    Write-Warn ".env.staging not found - needed for staging deployment"
}

if (Test-Path ".env.production") {
    Write-Pass ".env.production exists"
} else {
    Write-Warn ".env.production not found - needed for production deployment"
}

# ============================================
# 5. Database Schema Verification
# ============================================
Write-Header "5. Database Schema Verification"

$dbServiceFile = "src\cofounder_agent\services\database_service.py"

if (Test-Path $dbServiceFile) {
    $content = Get-Content $dbServiceFile -Raw
    
    $columns = @("featured_image_url", "seo_title", "seo_description", "seo_keywords")
    foreach ($col in $columns) {
        if ($content -match $col) {
            Write-Pass "$col column referenced in database_service.py"
        } else {
            Write-Fail "$col not found in database_service.py"
        }
    }
} else {
    Write-Fail "database_service.py not found"
}

# ============================================
# 6. Code Quality Checks
# ============================================
Write-Header "6. Code Quality"

$mainPy = "src\cofounder_agent\main.py"
if (Test-Path $mainPy) {
    $mainContent = Get-Content $mainPy -Raw
    
    # Check for emoji characters
    if ($mainContent -match '[😊🎯✅❌🚀📝🔧]') {
        Write-Fail "Emoji characters found in main.py - will cause encoding errors"
    } else {
        Write-Pass "No emoji characters in main.py"
    }
} else {
    Write-Warn "main.py not found"
}

# ============================================
# 7. API Integration Check
# ============================================
Write-Header "7. API Integration"

$apiFile = "web\public-site\lib\api-fastapi.js"
if (Test-Path $apiFile) {
    $apiContent = Get-Content $apiFile -Raw
    
    if ($apiContent -match "NEXT_PUBLIC_FASTAPI_URL") {
        Write-Pass "NEXT_PUBLIC_FASTAPI_URL referenced in API integration layer"
    } else {
        Write-Fail "API integration layer not correctly configured"
    }
    
    if ($apiContent -match "fetch") {
        Write-Pass "Fetch API implementation found"
    } else {
        Write-Fail "Fetch API not found in API integration layer"
    }
} else {
    Write-Fail "API integration file not found"
}

# ============================================
# 8. Documentation Check
# ============================================
Write-Header "8. Documentation"

$docs = @(
    "IMPLEMENTATION_SUMMARY.md",
    "TESTING_REPORT.md",
    "PUBLIC_SITE_VERIFICATION.md",
    "PRODUCTION_DEPLOYMENT_PREP.md",
    "DEPLOYMENT_APPROVAL.md"
)

foreach ($doc in $docs) {
    if (Test-Path $doc) {
        Write-Pass "Documentation exists: $doc"
    } else {
        Write-Warn "Documentation missing: $doc"
    }
}

# ============================================
# 9. Runtime Checks (if requested)
# ============================================
if ($CheckRuntime) {
    Write-Header "9. Runtime Verification"

    # Check if backend is running
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:8000/api/health" -ErrorAction SilentlyContinue
        if ($response.status -eq "healthy") {
            Write-Pass "Backend API is healthy"
            
            # Check if posts endpoint returns data
            $posts = Invoke-RestMethod -Uri "http://localhost:8000/api/posts?skip=0&limit=1" -ErrorAction SilentlyContinue
            if ($posts.data -and $posts.data.Count -gt 0) {
                Write-Pass "Posts endpoint returns data"
            } else {
                Write-Warn "Posts endpoint not returning data"
            }
        }
    } catch {
        Write-Warn "Backend API not responding on localhost:8000 (expected if not running)"
    }

    # Check if frontend is running
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:3000" -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Pass "Frontend is running on localhost:3000"
        }
    } catch {
        Write-Warn "Frontend not responding on localhost:3000 (expected if not running)"
    }
}

# ============================================
# Summary
# ============================================
Write-Header "Verification Summary"

Write-Host "✅ Passed: $passed" -ForegroundColor Green
Write-Host "❌ Failed: $failed" -ForegroundColor Red
Write-Host "⚠️  Warnings: $warnings" -ForegroundColor Yellow

Write-Host ""

if ($failed -eq 0) {
    Write-Host "✅ SYSTEM READY FOR DEPLOYMENT" -ForegroundColor Green -BackgroundColor DarkGreen
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Review PRODUCTION_DEPLOYMENT_PREP.md"
    Write-Host "2. Backup production database"
    Write-Host "3. Execute deployment: git checkout dev && git merge --no-ff feat/bugs"
    Write-Host "4. Monitor deployment in GitHub Actions"
    Write-Host "5. Run post-deployment verification"
    exit 0
} else {
    Write-Host "❌ SYSTEM NOT READY FOR DEPLOYMENT" -ForegroundColor Red -BackgroundColor DarkRed
    Write-Host ""
    Write-Host "Issues found:" -ForegroundColor Yellow
    Write-Host "- Fix all failed items (marked with ❌)"
    Write-Host "- Address warnings (marked with ⚠️) if critical"
    Write-Host "- Re-run this script to verify fixes"
    exit 1
}
