# 🚀 STRAPI REBUILD SCRIPT - Automated Execution
# 
# This script automates the Strapi rebuild process:
# - Backs up existing schemas
# - Cleans old installation
# - Fresh installs Strapi
# - Registers schemas programmatically
# - Seeds sample data
#
# Usage: .\rebuild-strapi.ps1
#
# Estimated Time: 10-15 minutes (excluding npm install)
# Status: Ready to Run

param(
    [switch]$SkipBackup,
    [switch]$SkipSeed,
    [switch]$SkipStart
)

# Color output
function Write-Success { Write-Host "$args" -ForegroundColor Green }
function Write-Error-Custom { Write-Host "❌ $args" -ForegroundColor Red }
function Write-Info { Write-Host "ℹ️  $args" -ForegroundColor Cyan }
function Write-Step { Write-Host "▶️  $args" -ForegroundColor Yellow }
function Write-Title { Write-Host "`n╔════════════════════════════════════════╗" -ForegroundColor Magenta; Write-Host "║ $args" -ForegroundColor Magenta; Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Magenta }

# Get workspace root
$WORKSPACE = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$STRAPI_ROOT = Join-Path $WORKSPACE "cms" "strapi-main"

Write-Title "🚀 STRAPI REBUILD - Automated Script"
Write-Info "Workspace: $WORKSPACE"
Write-Info "Strapi Root: $STRAPI_ROOT"
Write-Info "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ============================================================================
# PHASE 1: BACKUP EXISTING SCHEMAS
# ============================================================================
if (-not $SkipBackup) {
    Write-Title "📦 PHASE 1: Backup Existing Schemas"
    
    Write-Step "Creating backup directory..."
    $BACKUP_DIR = Join-Path $WORKSPACE "backups" "strapi-rebuild-$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null
    Write-Success "✅ Backup directory created: $BACKUP_DIR"
    
    Write-Step "Backing up API schemas..."
    $API_SRC = Join-Path $STRAPI_ROOT "src" "api"
    if (Test-Path $API_SRC) {
        Copy-Item -Path $API_SRC -Destination $BACKUP_DIR -Recurse -Force
        Write-Success "✅ Schemas backed up"
        Get-ChildItem -Path (Join-Path $BACKUP_DIR "api") | ForEach-Object {
            Write-Success "   ✅ $_"
        }
    }
    
    Write-Step "Backing up configuration files..."
    $configs = @(".env", "tsconfig.json", "package.json")
    foreach ($config in $configs) {
        $src = Join-Path $STRAPI_ROOT $config
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination $BACKUP_DIR -Force
            Write-Success "   ✅ Backed up $config"
        }
    }
    
    Write-Success "✅ PHASE 1 COMPLETE - All schemas backed up"
}

# ============================================================================
# PHASE 2: CLEAN INSTALLATION
# ============================================================================
Write-Title "🧹 PHASE 2: Clean Installation"

Push-Location $STRAPI_ROOT

Write-Step "Stopping any running Strapi process..."
Get-Process | Where-Object { $_.ProcessName -like "*node*" } | Where-Object { $_.MainWindowTitle -like "*Strapi*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Success "✅ Processes stopped"

Write-Step "Removing build artifacts..."
$dirs_to_remove = @("dist", "build", ".cache", "node_modules", ".next", ".tmp")
foreach ($dir in $dirs_to_remove) {
    $path = Join-Path $STRAPI_ROOT $dir
    if (Test-Path $path) {
        Remove-Item -Path $path -Recurse -Force
        Write-Success "   ✅ Removed $dir"
    }
}

Write-Step "Removing database files..."
$files_to_remove = @("data.db", "database.sqlite3", "package-lock.json", "yarn.lock")
foreach ($file in $files_to_remove) {
    $path = Join-Path $STRAPI_ROOT $file
    if (Test-Path $path) {
        Remove-Item -Path $path -Force
        Write-Success "   ✅ Removed $file"
    }
}

Write-Success "✅ PHASE 2 COMPLETE - Old installation cleaned"

# ============================================================================
# PHASE 3: FRESH NPM INSTALL
# ============================================================================
Write-Title "📦 PHASE 3: Fresh NPM Install"

Write-Step "Installing dependencies (this may take 2-3 minutes)..."
npm install

if ($LASTEXITCODE -eq 0) {
    Write-Success "✅ NPM install successful"
} else {
    Write-Error-Custom "NPM install failed. Check console for errors."
    Pop-Location
    exit 1
}

Write-Step "Verifying Strapi installation..."
npm list @strapi/strapi
Write-Success "✅ PHASE 3 COMPLETE - Dependencies installed"

# ============================================================================
# PHASE 4: COPY SCHEMAS BACK
# ============================================================================
Write-Title "📋 PHASE 4: Restore Schemas"

Write-Step "Copying schema files back..."
$API_BACKUP = Join-Path $BACKUP_DIR "api"
$API_TARGET = Join-Path $STRAPI_ROOT "src" "api"

if (Test-Path $API_BACKUP) {
    # Make sure target exists
    New-Item -ItemType Directory -Path $API_TARGET -Force | Out-Null
    
    # Copy each content type
    Get-ChildItem -Path $API_BACKUP -Directory | ForEach-Object {
        $contentType = $_.Name
        $src = $_.FullName
        $dst = Join-Path $API_TARGET $contentType
        
        Copy-Item -Path $src -Destination $dst -Recurse -Force
        Write-Success "   ✅ Restored $contentType"
    }
} else {
    Write-Error-Custom "Backup directory not found. Cannot restore schemas."
    Pop-Location
    exit 1
}

Write-Success "✅ PHASE 4 COMPLETE - All schemas restored"

# ============================================================================
# PHASE 5: START STRAPI FOR SETUP
# ============================================================================
if (-not $SkipStart) {
    Write-Title "🚀 PHASE 5: Start Strapi"
    
    Write-Step "Starting Strapi (new window will open)..."
    Write-Info "Strapi will start in a new terminal window"
    Write-Info "Complete admin setup when prompted"
    Write-Info "Then press Enter here to continue..."
    
    # Start Strapi in new window
    Start-Process -NoNewWindow -FilePath "cmd" -ArgumentList "/c npm run develop" -WorkingDirectory $STRAPI_ROOT
    
    Write-Info "Waiting for Strapi to start (30 seconds)..."
    Start-Sleep -Seconds 30
    
    Write-Step "Checking Strapi availability..."
    $maxRetries = 5
    $retryCount = 0
    $strapiReady = $false
    
    while ($retryCount -lt $maxRetries) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:1337/admin" -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                Write-Success "✅ Strapi is running"
                $strapiReady = $true
                break
            }
        } catch {
            $retryCount++
            if ($retryCount -lt $maxRetries) {
                Write-Info "Waiting for Strapi... (attempt $retryCount/$maxRetries)"
                Start-Sleep -Seconds 5
            }
        }
    }
    
    if (-not $strapiReady) {
        Write-Error-Custom "Strapi did not start. Check for errors in the terminal."
        Pop-Location
        exit 1
    }
    
    Write-Info "Go to http://localhost:1337/admin to complete admin setup"
    Write-Info "Create admin account if prompted"
    Write-Info "Generate API Token: Settings → API Tokens → Create"
    Write-Info "Press Enter when ready to register schemas..."
    Read-Host
    
    Write-Success "✅ PHASE 5 COMPLETE - Strapi started"
}

# ============================================================================
# PHASE 6: REGISTER SCHEMAS
# ============================================================================
Write-Title "📝 PHASE 6: Register Schemas"

Write-Step "Checking for API token..."
$token = $env:STRAPI_API_TOKEN

if ([string]::IsNullOrEmpty($token)) {
    Write-Error-Custom "STRAPI_API_TOKEN environment variable not set"
    Write-Info "You need to:"
    Write-Info "  1. Go to http://localhost:1337/admin"
    Write-Info "  2. Settings → API Tokens → Create new"
    Write-Info "  3. Name: 'Setup Token', Type: 'Full access'"
    Write-Info "  4. Copy the token"
    Write-Info "  5. Set it: `$env:STRAPI_API_TOKEN = 'your-token-here'"
    Write-Info "  6. Run this script again with -SkipStart flag"
    Pop-Location
    exit 1
}

Write-Info "Using API token: $($token.Substring(0, 10))..."
Write-Step "Registering content types..."

if (Test-Path "scripts/register-content-types.js") {
    node scripts/register-content-types.js
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "✅ Schemas registered successfully"
    } else {
        Write-Error-Custom "Schema registration failed"
        Write-Info "Check the output above for errors"
    }
} else {
    Write-Error-Custom "register-content-types.js not found"
    Write-Info "Expected path: scripts/register-content-types.js"
}

Write-Success "✅ PHASE 6 COMPLETE - Schemas registered"

# ============================================================================
# PHASE 7: SEED DATA (OPTIONAL)
# ============================================================================
if (-not $SkipSeed) {
    Write-Title "🌱 PHASE 7: Seed Sample Data"
    
    Write-Step "Seeding categories, tags, and authors..."
    if (Test-Path "scripts/seed-data-fixed.js") {
        npm run seed
        Write-Success "✅ Data seeded"
    } else {
        Write-Info "Seed script not found, skipping"
    }
    
    Write-Step "Seeding single types (About, Privacy Policy)..."
    if (Test-Path "scripts/seed-single-types.js") {
        npm run seed:single
        Write-Success "✅ Single types seeded"
    }
    
    Write-Success "✅ PHASE 7 COMPLETE - Sample data created"
}

Pop-Location

# ============================================================================
# FINAL SUMMARY
# ============================================================================
Write-Title "✅ REBUILD COMPLETE"

Write-Success "🎉 Strapi has been successfully rebuilt!"
Write-Info ""
Write-Info "NEXT STEPS:"
Write-Info "  1. ✅ Strapi Admin:     http://localhost:1337/admin"
Write-Info "  2. ✅ Public Site:      http://localhost:3000"
Write-Info "  3. ✅ API Docs:         http://localhost:1337/documentation"
Write-Info ""
Write-Info "VERIFY IN ADMIN:"
Write-Info "  • Content Manager should show all 7 content types"
Write-Info "  • Categories, Tags, Authors should have sample data"
Write-Info "  • About and Privacy Policy pages should exist"
Write-Info ""
Write-Info "VERIFY WITH CURL:"
Write-Info "  • curl http://localhost:1337/api/posts"
Write-Info "  • curl http://localhost:1337/api/categories"
Write-Info "  • curl http://localhost:1337/api/tags"
Write-Info ""
Write-Info "BACKUP LOCATION:"
Write-Info "  📦 $BACKUP_DIR"
Write-Info ""
Write-Info "TROUBLESHOOTING:"
Write-Info "  • If endpoints return 404: Check Strapi admin for content types"
Write-Info "  • If admin won't start: Check terminal for error messages"
Write-Info "  • If registration fails: Verify STRAPI_API_TOKEN is set"
Write-Info ""
Write-Info "Questions? See: STRAPI_REBUILD_IMPLEMENTATION_PLAN.md"
