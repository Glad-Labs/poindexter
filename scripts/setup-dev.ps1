#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Automated development environment setup for GLAD Labs monorepo.
    
.DESCRIPTION
    Installs all dependencies and fixes common monorepo issues:
    - Installs root node_modules (@strapi/strapi fix)
    - Installs workspace dependencies
    - Verifies SQLite setup
    - Creates .env from .env.example if needed
    - Validates module resolution chain
    
.PARAMETER Clean
    Removes node_modules and reinstalls everything from scratch.
    
.PARAMETER SkipEnv
    Skips .env file creation (use existing .env if present).
    
.PARAMETER Verbose
    Shows detailed output for debugging.
    
.EXAMPLE
    .\scripts\setup-dev.ps1                    # Standard setup
    .\scripts\setup-dev.ps1 -Clean              # Clean install
    .\scripts\setup-dev.ps1 -SkipEnv             # Skip .env creation
    .\scripts\setup-dev.ps1 -Clean -Verbose     # Clean + debug output
    
.NOTES
    Author: GLAD Labs Development Team
    Date: October 25, 2025
    This script must be run from the project root directory.
#>

param(
    [switch]$Clean = $false,
    [switch]$SkipEnv = $false,
    [switch]$Verbose = $false
)

$ErrorActionPreference = "Stop"
$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot } else { Get-Location }

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

function Write-Header {
    param([string]$Title)
    Write-Host "`n" + ("=" * 70) -ForegroundColor Cyan
    Write-Host "🚀 $Title" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message, [int]$Step)
    Write-Host "`n[STEP $Step] $Message" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Message)
    Write-Host "✅ $Message" -ForegroundColor Green
}

function Write-Error {
    param([string]$Message)
    Write-Host "❌ $Message" -ForegroundColor Red
}

function Write-Info {
    param([string]$Message)
    Write-Host "ℹ️  $Message" -ForegroundColor Blue
}

function Write-Debug {
    param([string]$Message)
    if ($Verbose) {
        Write-Host "🔍 DEBUG: $Message" -ForegroundColor Gray
    }
}

function Test-Command {
    param([string]$Command)
    try {
        Get-Command $Command -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Test-DirectoryExists {
    param([string]$Path, [string]$Name)
    if (Test-Path $Path) {
        Write-Success "$Name found at: $Path"
        return $true
    }
    else {
        Write-Error "$Name not found at: $Path"
        return $false
    }
}

# ============================================================================
# VALIDATION
# ============================================================================

Write-Header "GLAD Labs Development Environment Setup"
Write-Info "Project Root: $projectRoot"
Write-Info "PowerShell Version: $($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)"

# Verify we're in the project root
if (-not (Test-Path "$projectRoot/package.json")) {
    Write-Error "package.json not found! Please run this script from the project root."
    exit 1
}

# Check prerequisites
Write-Step "Checking prerequisites" 1

if (-not (Test-Command "node")) {
    Write-Error "Node.js not found! Please install Node.js v20.11.1 or later"
    exit 1
}
Write-Success "Node.js: $(node --version)"

if (-not (Test-Command "npm")) {
    Write-Error "npm not found! Please install npm"
    exit 1
}
Write-Success "npm: $(npm --version)"

if (-not (Test-Command "git")) {
    Write-Info "Git not found, but continuing (optional for this setup)"
}
else {
    Write-Success "Git: $(git --version)"
}

# ============================================================================
# CLEAN MODE (Optional)
# ============================================================================

if ($Clean) {
    Write-Step "Cleaning node_modules and package-lock.json (--clean flag set)" 2
    
    # Remove root node_modules
    if (Test-Path "$projectRoot/node_modules") {
        Write-Info "Removing $projectRoot/node_modules..."
        Remove-Item -Path "$projectRoot/node_modules" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Success "Root node_modules removed"
    }
    
    # Remove Strapi node_modules
    if (Test-Path "$projectRoot/cms/strapi-main/node_modules") {
        Write-Info "Removing $projectRoot/cms/strapi-main/node_modules..."
        Remove-Item -Path "$projectRoot/cms/strapi-main/node_modules" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Success "Strapi node_modules removed"
    }
    
    # Remove lock files
    if (Test-Path "$projectRoot/package-lock.json") {
        Write-Debug "Removing $projectRoot/package-lock.json"
        Remove-Item -Path "$projectRoot/package-lock.json" -Force
    }
    
    if (Test-Path "$projectRoot/cms/strapi-main/package-lock.json") {
        Write-Debug "Removing $projectRoot/cms/strapi-main/package-lock.json"
        Remove-Item -Path "$projectRoot/cms/strapi-main/package-lock.json" -Force
    }
    
    Write-Success "Clean complete"
}

# ============================================================================
# ENV SETUP (Optional)
# ============================================================================

if (-not $SkipEnv) {
    Write-Step "Setting up environment configuration" 3
    
    if (Test-Path "$projectRoot/.env") {
        Write-Info ".env already exists, skipping creation"
    }
    elseif (Test-Path "$projectRoot/.env.example") {
        Write-Info "Creating .env from .env.example..."
        Copy-Item -Path "$projectRoot/.env.example" -Destination "$projectRoot/.env"
        Write-Success ".env created from template"
    }
    else {
        Write-Error ".env.example not found!"
        exit 1
    }
}
else {
    Write-Info "Skipping .env setup (--SkipEnv flag set)"
}

# ============================================================================
# ROOT NPM INSTALL (CRITICAL FIX FOR MONOREPO)
# ============================================================================

Write-Step "Installing root dependencies (this fixes @strapi/strapi module resolution)" 4

Write-Info "Running: npm install at $projectRoot"
Write-Debug "This installs the monorepo-level dependencies and enables workspace hoisting"

try {
    Push-Location $projectRoot
    npm install --no-save 2>&1 | ForEach-Object {
        Write-Debug $_
    }
    
    # Verify @strapi/strapi is now in root node_modules
    if (Test-Path "$projectRoot/node_modules/@strapi/strapi/package.json") {
        Write-Success "@strapi/strapi found in root node_modules ✓"
    }
    else {
        Write-Error "@strapi/strapi NOT found in root node_modules - module resolution chain broken!"
        exit 1
    }
}
catch {
    Write-Error "npm install failed: $_"
    Pop-Location
    exit 1
}
finally {
    Pop-Location
}

# ============================================================================
# INSTALL @strapi/strapi IF MISSING (THE BREAKTHROUGH FIX)
# ============================================================================

Write-Step "Ensuring @strapi/strapi is installed (monorepo breakthrough fix)" 5

if (-not (Test-Path "$projectRoot/node_modules/@strapi/strapi/package.json")) {
    Write-Info "Installing @strapi/strapi@^5.18.1 at root level..."
    
    try {
        Push-Location $projectRoot
        npm install @strapi/strapi@^5.18.1 --save-dev 2>&1 | ForEach-Object {
            Write-Debug $_
        }
        Write-Success "@strapi/strapi installed successfully"
    }
    catch {
        Write-Error "Failed to install @strapi/strapi: $_"
        Pop-Location
        exit 1
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Success "@strapi/strapi already installed"
}

# ============================================================================
# WORKSPACE DEPENDENCIES
# ============================================================================

Write-Step "Installing workspace dependencies" 6

try {
    Push-Location $projectRoot
    npm install --workspaces 2>&1 | ForEach-Object {
        Write-Debug $_
    }
    Write-Success "All workspace dependencies installed"
}
catch {
    Write-Error "Workspace installation failed: $_"
    Pop-Location
    exit 1
}
finally {
    Pop-Location
}

# ============================================================================
# STRAPI-SPECIFIC SETUP
# ============================================================================

Write-Step "Setting up Strapi CMS" 7

$strapiPath = "$projectRoot/cms/strapi-main"

try {
    Push-Location $strapiPath
    
    # Ensure sqlite3 drivers are installed
    Write-Info "Verifying SQLite drivers..."
    npm list sqlite3 --depth=0 | Out-Null
    
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Installing sqlite3 drivers..."
        npm install sqlite3 better-sqlite3 2>&1 | ForEach-Object {
            Write-Debug $_
        }
        Write-Success "SQLite drivers installed"
    }
    else {
        Write-Success "SQLite drivers already installed"
    }
}
catch {
    Write-Error "Strapi setup failed: $_"
    Pop-Location
    exit 1
}
finally {
    Pop-Location
}

# ============================================================================
# VERIFICATION
# ============================================================================

Write-Step "Verifying setup" 8

$verificationsPassed = $true

# Check 1: Root node_modules has @strapi/strapi
if (Test-Path "$projectRoot/node_modules/@strapi/strapi/package.json") {
    Write-Success "@strapi/strapi in root node_modules"
}
else {
    Write-Error "@strapi/strapi NOT in root node_modules"
    $verificationsPassed = $false
}

# Check 2: Strapi workspace node_modules exists
if (Test-Path "$projectRoot/cms/strapi-main/node_modules") {
    Write-Success "Strapi workspace node_modules exists"
}
else {
    Write-Error "Strapi workspace node_modules missing"
    $verificationsPassed = $false
}

# Check 3: SQLite database config exists
if (Test-Path "$projectRoot/cms/strapi-main/config/database.js") {
    Write-Success "Strapi database config found"
}
else {
    Write-Error "Strapi database config missing"
    $verificationsPassed = $false
}

# Check 4: .env file exists
if (Test-Path "$projectRoot/.env") {
    Write-Success ".env configuration file exists"
}
else {
    Write-Info ".env not found (will be created on first service start)"
}

# ============================================================================
# SUMMARY
# ============================================================================

Write-Header "Setup Complete!"

if ($verificationsPassed) {
    Write-Success "All checks passed! Development environment is ready."
    Write-Host "`n📋 Next Steps:" -ForegroundColor Cyan
    Write-Host "   1. Review and edit .env if needed"
    Write-Host "   2. Start services: npm run dev"
    Write-Host "   3. Access Strapi admin: http://localhost:1337/admin"
    Write-Host "   4. Run tests: npm test`n" -ForegroundColor Green
}
else {
    Write-Error "Some checks failed. Please review the errors above."
    exit 1
}

Write-Info "For more information, see: docs/MONOREPO_SETUP.md"
Write-Host "`n"
