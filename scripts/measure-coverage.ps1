##############################################################################
# Coverage Measurement Script for Glad Labs (Windows PowerShell)
# 
# Measures test coverage and generates reports with >80% threshold enforcement
# 
# Usage:
#   .\scripts\measure-coverage.ps1 [html|json|term|all]
#
# Examples:
#   .\scripts\measure-coverage.ps1 html      # Generate HTML report
#   .\scripts\measure-coverage.ps1 json      # Generate JSON report
#   .\scripts\measure-coverage.ps1 term      # Print terminal report
#   .\scripts\measure-coverage.ps1 all       # Generate all reports
##############################################################################

param(
    [Parameter(Mandatory=$false)]
    [string]$ReportType = "all",
    
    [Parameter(Mandatory=$false)]
    [int]$Threshold = 80
)

# Color functions
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# Configuration
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot "src\cofounder_agent"
$TestPath = Join-Path $ProjectRoot "src\cofounder_agent\tests"
$CoverageThreshold = $Threshold

# ============================================================================
# Helper Functions
# ============================================================================

function Test-Dependencies {
    Write-Info "Checking dependencies..."
    
    # Check Python
    try {
        $pythonVersion = python --version 2>&1
        Write-Success "Python found: $pythonVersion"
    }
    catch {
        Write-Error "Python is not installed or not in PATH"
        exit 1
    }
    
    # Check coverage.py
    try {
        python -c "import coverage" 2>$null
        Write-Success "coverage.py is installed"
    }
    catch {
        Write-Warning "coverage.py is not installed. Installing..."
        pip install coverage
    }
    
    # Check pytest
    try {
        python -c "import pytest" 2>$null
        Write-Success "pytest is installed"
    }
    catch {
        Write-Error "pytest is not installed. Please run: pip install pytest"
        exit 1
    }
}

function Measure-Coverage {
    Write-Info "Measuring test coverage..."
    Write-Info "Python path: $PythonPath"
    Write-Info "Test path: $TestPath"
    ""
    
    Push-Location $ProjectRoot
    
    try {
        # Run tests with coverage measurement
        $env:PYTHONPATH = $ProjectRoot
        coverage run `
            --source="$PythonPath" `
            --omit="*/tests/*,*/test_*.py,*/__pycache__/*" `
            -m pytest `
            "$TestPath" `
            -v `
            --tb=short `
            -m "not slow"
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Tests completed successfully"
        }
        else {
            Write-Error "Some tests failed"
            exit 1
        }
    }
    finally {
        Pop-Location
    }
}

function Generate-TerminalReport {
    Write-Info "Generating terminal coverage report..."
    ""
    
    coverage report `
        --fail-under=$CoverageThreshold `
        --precision=2 `
        --show-missing `
        --skip-covered
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Coverage threshold met (>=$CoverageThreshold%)"
        return $true
    }
    else {
        Write-Error "Coverage below threshold ($CoverageThreshold%)"
        return $false
    }
}

function Generate-HtmlReport {
    Write-Info "Generating HTML coverage report..."
    
    coverage html
    
    $htmlPath = Join-Path $ProjectRoot "htmlcov\index.html"
    if (Test-Path $htmlPath) {
        Write-Success "HTML report generated: $htmlPath"
        
        # Try to open in default browser
        try {
            Start-Process $htmlPath
        }
        catch {
            Write-Warning "Could not open HTML report in browser"
        }
        
        return $true
    }
    else {
        Write-Error "Failed to generate HTML report"
        return $false
    }
}

function Generate-JsonReport {
    Write-Info "Generating JSON coverage report..."
    
    coverage json
    
    $jsonPath = Join-Path $ProjectRoot "coverage.json"
    if (Test-Path $jsonPath) {
        Write-Success "JSON report generated: $jsonPath"
        
        # Parse and display summary
        try {
            $content = Get-Content $jsonPath -Raw | ConvertFrom-Json
            $summary = $content.totals
            
            Write-Host ""
            Write-Host "📊 Coverage Summary:" -ForegroundColor Cyan
            Write-Host "   Overall: $([math]::Round($summary.percent_covered, 1))%" 
            Write-Host "   Lines covered: $($summary.covered_lines)"
            Write-Host "   Lines missing: $($summary.missing_lines)"
            Write-Host "   Total lines: $($summary.num_statements)"
        }
        catch {
            Write-Warning "Could not parse coverage.json: $_"
        }
        
        return $true
    }
    else {
        Write-Error "Failed to generate JSON report"
        return $false
    }
}

function Generate-XmlReport {
    Write-Info "Generating XML coverage report (for CI/CD)..."
    
    coverage xml
    
    $xmlPath = Join-Path $ProjectRoot "coverage.xml"
    if (Test-Path $xmlPath) {
        Write-Success "XML report generated: $xmlPath"
        return $true
    }
    else {
        Write-Error "Failed to generate XML report"
        return $false
    }
}

function Display-Summary {
    ""
    Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║         Coverage Measurement Complete                      ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    ""
    
    Write-Host "Generated Reports:" -ForegroundColor Green
    
    if (Test-Path (Join-Path $ProjectRoot "htmlcov\index.html")) {
        Write-Host "  ✓ HTML:   $(Join-Path $ProjectRoot "htmlcov\index.html")"
    }
    
    if (Test-Path (Join-Path $ProjectRoot "coverage.json")) {
        Write-Host "  ✓ JSON:   $(Join-Path $ProjectRoot "coverage.json")"
    }
    
    if (Test-Path (Join-Path $ProjectRoot "coverage.xml")) {
        Write-Host "  ✓ XML:    $(Join-Path $ProjectRoot "coverage.xml")"
    }
    
    ""
    Write-Host "Threshold: $CoverageThreshold%"
    ""
    Write-Host "To view detailed report: Start-Process htmlcov\index.html"
    ""
}

# ============================================================================
# Main Execution
# ============================================================================

function Main {
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "Glad Labs - Test Coverage Measurement" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""
    
    Test-Dependencies
    Measure-Coverage
    
    # Generate requested reports
    $success = $true
    
    switch ($ReportType.ToLower()) {
        "html" {
            $success = $success -and (Generate-HtmlReport)
        }
        "json" {
            $success = $success -and (Generate-JsonReport)
        }
        "xml" {
            $success = $success -and (Generate-XmlReport)
        }
        "term" {
            $success = $success -and (Generate-TerminalReport)
        }
        "all" {
            $success = $success -and (Generate-TerminalReport)
            $success = $success -and (Generate-HtmlReport)
            $success = $success -and (Generate-JsonReport)
            $success = $success -and (Generate-XmlReport)
        }
        default {
            Write-Error "Unknown report type: $ReportType"
            Write-Host "Usage: measure-coverage.ps1 [html|json|xml|term|all]"
            exit 1
        }
    }
    
    Display-Summary
    
    if ($success) {
        exit 0
    }
    else {
        exit 1
    }
}

# Execute main function
Main
