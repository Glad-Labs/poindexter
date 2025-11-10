#!/usr/bin/env powershell
# Glad Labs AI Co-Founder System - Comprehensive Dependency Setup
param(
    [switch]$Clean = $false,
    [switch]$PythonOnly = $false,
    [switch]$NodeOnly = $false,
    [switch]$Verbose = $false
)

Write-Host "🚀 Glad Labs AI Co-Founder System - Dependency Setup" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

if ($Verbose) {
    $VerbosePreference = "Continue"
}

function Write-Section {
    param([string]$Title)
    Write-Host "`n🔧 $Title" -ForegroundColor Yellow
    Write-Host ("=" * 50) -ForegroundColor Yellow
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

# Change to project root
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot
Write-Info "Working directory: $PWD"

# Clean installation if requested
if ($Clean) {
    Write-Section "Cleaning Previous Installations"
    
    Write-Host "Removing node_modules and Python cache..." -ForegroundColor Yellow
    
    # Remove Node.js artifacts
    if (Test-Path "node_modules") { 
        Remove-Item "node_modules" -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path "package-lock.json") { 
        Remove-Item "package-lock.json" -Force -ErrorAction SilentlyContinue
    }
    
    # Remove Python artifacts
    Get-ChildItem -Path . -Name "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path . -Name "*.pyc" -Recurse -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    
    Write-Success "Cleanup completed"
}

# Check prerequisites
Write-Section "Checking Prerequisites"

$Prerequisites = @{
    "Node.js" = "node"
    "npm" = "npm" 
    "Python" = "python"
    "pip" = "pip"
}

$MissingPrereqs = @()

foreach ($prereq in $Prerequisites.GetEnumerator()) {
    if (Test-Command $prereq.Value) {
        try {
            if ($prereq.Key -eq "Node.js") {
                $version = (node --version 2>$null)
            }
            elseif ($prereq.Key -eq "npm") {
                $version = (npm --version 2>$null)
            }
            elseif ($prereq.Key -eq "Python") {
                $version = (python --version 2>$null)
            }
            elseif ($prereq.Key -eq "pip") {
                $version = (pip --version 2>$null)
            }
            Write-Success "$($prereq.Key) found: $version"
        }
        catch {
            Write-Success "$($prereq.Key) found"
        }
    }
    else {
        $MissingPrereqs += $prereq.Key
        Write-Error "$($prereq.Key) not found"
    }
}

if ($MissingPrereqs.Count -gt 0) {
    Write-Error "Missing prerequisites: $($MissingPrereqs -join ', ')"
    Write-Host "`nPlease install the missing prerequisites and run this script again." -ForegroundColor Red
    exit 1
}

# Install Python dependencies
if (-not $NodeOnly) {
    Write-Section "Installing Python Dependencies"
    
    try {
        Write-Host "Upgrading pip..." -ForegroundColor Yellow
        python -m pip install --upgrade pip
        
        Write-Host "Installing core monorepo Python requirements..." -ForegroundColor Yellow
        if (Test-Path "requirements.txt") {
            pip install -r requirements.txt
            Write-Success "Core Python dependencies installed"
        }
        
        Write-Host "Installing AI Co-Founder system requirements..." -ForegroundColor Yellow
        if (Test-Path "src/cofounder_agent/requirements.txt") {
            pip install -r src/cofounder_agent/requirements.txt
            Write-Success "AI Co-Founder Python dependencies installed"
        }
        
        # Verify key Python packages
        Write-Host "Verifying Python installation..." -ForegroundColor Yellow
        $pythonPackages = @("fastapi", "openai", "pytest")
        foreach ($package in $pythonPackages) {
            try {
                $result = python -c "import $package; print('OK')" 2>$null
                if ($result -eq "OK") {
                    Write-Success "✓ $package"
                }
                else {
                    Write-Error "✗ $package verification failed"
                }
            }
            catch {
                Write-Error "✗ $package not properly installed"
            }
        }
    }
    catch {
        Write-Error "Failed to install Python dependencies: $($_.Exception.Message)"
    }
}

# Install Node.js dependencies
if (-not $PythonOnly) {
    Write-Section "Installing Node.js Dependencies"
    
    try {
        Write-Host "Installing root dependencies..." -ForegroundColor Yellow
        npm install
        Write-Success "Root dependencies installed"
        
        Write-Host "Installing workspace dependencies..." -ForegroundColor Yellow
        npm install --workspaces
        Write-Success "Workspace dependencies installed"
        
        # Verify each workspace
        $workspaces = @(
            @{Path="web/public-site"; Name="Public Site"}
            @{Path="web/oversight-hub"; Name="Oversight Hub"}  
            @{Path="cms/strapi-main"; Name="Strapi CMS"}
        )
        
        Write-Host "Verifying workspace installations..." -ForegroundColor Yellow
        foreach ($workspace in $workspaces) {
            if (Test-Path "$($workspace.Path)/package.json") {
                Write-Success "✓ $($workspace.Name): package.json found"
            }
            else {
                Write-Error "✗ $($workspace.Name): package.json not found"
            }
        }
    }
    catch {
        Write-Error "Failed to install Node.js dependencies: $($_.Exception.Message)"
    }
}

# Create convenience scripts
Write-Section "Creating Development Scripts"

$startScript = @'
Write-Host "🚀 Starting Glad Labs AI Co-Founder System..." -ForegroundColor Cyan

# Start services
Write-Host "Starting services in background..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'web/public-site'; npm run dev"
Start-Sleep 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'web/oversight-hub'; npm start"  
Start-Sleep 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'cms/strapi-main'; npm run develop"

Write-Host "✅ Services starting..." -ForegroundColor Green
Write-Host "🌐 Access points:" -ForegroundColor Yellow
Write-Host "   - Public Site: http://localhost:3000" -ForegroundColor White
Write-Host "   - Oversight Hub: http://localhost:3001" -ForegroundColor White
Write-Host "   - Strapi CMS: http://localhost:1337" -ForegroundColor White
'@

$startScript | Out-File -FilePath "start-system.ps1" -Encoding UTF8
Write-Success "Created start-system.ps1"

# Final summary
Write-Section "Installation Complete"

Write-Host "🎉 Dependency installation completed!" -ForegroundColor Green
Write-Host ""
Write-Host "📋 What was installed:" -ForegroundColor Yellow
if (-not $NodeOnly) {
    Write-Host "   ✅ Python dependencies (AI/ML, FastAPI, testing)" -ForegroundColor White
}
if (-not $PythonOnly) {
    Write-Host "   ✅ Node.js dependencies (Next.js, React, Strapi)" -ForegroundColor White
}
Write-Host ""
Write-Host "🚀 Quick start options:" -ForegroundColor Yellow
Write-Host "   • Run all services: .\start-system.ps1" -ForegroundColor White
Write-Host "   • Development mode: npm run dev" -ForegroundColor White
Write-Host ""
Write-Host "🎯 Your AI Co-Founder system is ready!" -ForegroundColor Cyan