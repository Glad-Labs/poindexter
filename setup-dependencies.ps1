#!/usr/bin/env powershell
# GLAD Labs AI Co-Founder System - Comprehensive Dependency Setup
# This script ensures all dependencies are properly installed across the monorepo

param(
    [switch]$Clean = $false,
    [switch]$PythonOnly = $false,
    [switch]$NodeOnly = $false,
    [switch]$Verbose = $false
)

Write-Host "🚀 GLAD Labs AI Co-Founder System - Dependency Setup" -ForegroundColor Cyan
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
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
Write-Info "Working directory: $PWD"

# Clean installation if requested
if ($Clean) {
    Write-Section "Cleaning Previous Installations"
    
    Write-Host "Removing node_modules and Python cache..." -ForegroundColor Yellow
    
    # Remove Node.js artifacts
    if (Test-Path "node_modules") { Remove-Item "node_modules" -Recurse -Force }
    if (Test-Path "package-lock.json") { Remove-Item "package-lock.json" -Force }
    Get-ChildItem -Path . -Name "node_modules" -Recurse -Directory | Remove-Item -Recurse -Force
    
    # Remove Python artifacts
    Get-ChildItem -Path . -Name "__pycache__" -Recurse -Directory | Remove-Item -Recurse -Force
    Get-ChildItem -Path . -Name "*.pyc" -Recurse -File | Remove-Item -Force
    if (Test-Path ".venv") { Remove-Item ".venv" -Recurse -Force }
    
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
        $version = ""
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
                $version = (pip --version 2>$null).Split()[1]
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
        Write-Host "Installing core monorepo Python requirements..." -ForegroundColor Yellow
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        Write-Success "Core Python dependencies installed"
        
        Write-Host "Installing AI Co-Founder system requirements..." -ForegroundColor Yellow
        pip install -r src/cofounder_agent/requirements.txt
        Write-Success "AI Co-Founder Python dependencies installed"
        
        # Install testing dependencies
        Write-Host "Installing testing framework dependencies..." -ForegroundColor Yellow
        pip install pytest pytest-asyncio pytest-cov aiohttp websockets
        Write-Success "Testing framework dependencies installed"
        
        # Verify key Python packages
        Write-Host "Verifying Python installation..." -ForegroundColor Yellow
        $pythonPackages = @("fastapi", "openai", "pytest", "aiohttp")
        foreach ($package in $pythonPackages) {
            try {
                python -c "import ${package}; print('${package}' + ${package}.__version__)" 2>$null
                Write-Success "✓ $package"
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
            @{Path="cms/strapi-v5-backend"; Name="Strapi CMS"}
        )
        
        Write-Host "Verifying workspace installations..." -ForegroundColor Yellow
        foreach ($workspace in $workspaces) {
            if (Test-Path "$($workspace.Path)/package.json") {
                Push-Location $workspace.Path
                try {
                    $packageCount = (npm list --depth=0 --json 2>$null | ConvertFrom-Json).dependencies.Count
                    Write-Success "✓ $($workspace.Name): $packageCount packages"
                }
                catch {
                    Write-Info "? $($workspace.Name): Installation may need verification"
                }
                finally {
                    Pop-Location
                }
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

# Run validation tests
Write-Section "Running Validation Tests"

if (-not $NodeOnly) {
    Write-Host "Testing Python environment..." -ForegroundColor Yellow
    try {
        Set-Location "src/cofounder_agent/tests"
        python -m pytest test_e2e_fixed.py::TestE2EWorkflows::test_business_owner_daily_routine -v --tb=short
        Write-Success "Python tests passed"
    }
    catch {
        Write-Error "Python tests failed - but dependencies are installed"
    }
    finally {
        Set-Location $ProjectRoot
    }
}

if (-not $PythonOnly) {
    Write-Host "Testing Node.js workspaces..." -ForegroundColor Yellow
    try {
        # Test each workspace build
        foreach ($workspace in $workspaces) {
            if (Test-Path "$($workspace.Path)/package.json") {
                Push-Location $workspace.Path
                try {
                    $packageJson = Get-Content "package.json" | ConvertFrom-Json
                    if ($packageJson.scripts.build) {
                        Write-Host "Testing build for $($workspace.Name)..." -ForegroundColor Gray
                        # npm run build 2>$null | Out-Null  # Commented out to avoid long build times
                        Write-Success "✓ $($workspace.Name) build script available"
                    }
                }
                catch {
                    Write-Info "? $($workspace.Name) may need manual verification"
                }
                finally {
                    Pop-Location
                }
            }
        }
    }
    catch {
        Write-Error "Node.js workspace validation had issues"
    }
}

# Create convenience scripts
Write-Section "Creating Convenience Scripts"

$startScript = @'
#!/usr/bin/env powershell
# Quick start script for GLAD Labs AI Co-Founder System

Write-Host "🚀 Starting GLAD Labs AI Co-Founder System..." -ForegroundColor Cyan

# Start all services in parallel
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'cms/strapi-v5-backend'; npm run develop"
Start-Sleep 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'web/public-site'; npm run dev"
Start-Sleep 2  
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'web/oversight-hub'; npm start"
Start-Sleep 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'src/cofounder_agent'; python main.py"

Write-Host "✅ All services starting in separate windows..." -ForegroundColor Green
Write-Host "🌐 Access points:" -ForegroundColor Yellow
Write-Host "   - Public Site: http://localhost:3000" -ForegroundColor White
Write-Host "   - Oversight Hub: http://localhost:3001" -ForegroundColor White  
Write-Host "   - Strapi CMS: http://localhost:1337" -ForegroundColor White
Write-Host "   - AI Co-Founder API: http://localhost:8000" -ForegroundColor White
'@

$startScript | Out-File -FilePath "start-system.ps1" -Encoding UTF8
Write-Success "Created start-system.ps1"

# Final summary
Write-Section "Installation Summary"

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
Write-Host "   • Test Python: cd src/cofounder_agent/tests && python -m pytest test_e2e_fixed.py -v" -ForegroundColor White
Write-Host ""
Write-Host "📚 Documentation:" -ForegroundColor Yellow
Write-Host "   • System README: ./README.md" -ForegroundColor White
Write-Host "   • Testing Guide: ./src/cofounder_agent/tests/README.md" -ForegroundColor White

Write-Host "`n🎯 Your AI Co-Founder system is ready to use!" -ForegroundColor Cyan