# Content Pipeline Validation Suite Runner (Windows PowerShell)
# Comprehensive testing of edge cases and pipeline workflows
# Usage: .\scripts\run-validation-suite.ps1 -Mode full

param(
    [ValidateSet("full", "quick", "edge-cases", "performance")]
    [string]$Mode = "full"
)

# Colors
$colors = @{
    "Header"  = "Cyan"
    "Success" = "Green"
    "Error"   = "Red"
    "Warning" = "Yellow"
    "Info"    = "Blue"
}

function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor $colors["Info"]
    Write-Host "║  $($Text.PadRight(60))║" -ForegroundColor $colors["Info"]
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor $colors["Info"]
    Write-Host ""
}

function Write-SectionHeader {
    param([string]$Text)
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor $colors["Info"]
    Write-Host "  $Text" -ForegroundColor $colors["Info"]
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor $colors["Info"]
}

function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Run-TestSuite {
    param(
        [string]$TestClass,
        [string]$Description,
        [string]$CofoundeDir
    )
    
    Write-Host "  Testing $Description..." -ForegroundColor $colors["Warning"]
    $output = & python -m pytest "$CofoundeDir/tests/test_content_pipeline_comprehensive.py::$TestClass" -v --tb=short 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    ✅ $Description tests passed" -ForegroundColor $colors["Success"]
        return $true
    } else {
        Write-Host "    ❌ $Description tests failed" -ForegroundColor $colors["Error"]
        Write-Host $output
        return $false
    }
}

# Main execution
Clear-Host
Write-Header "Content Pipeline Validation Suite"

# Get directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$CofoundeDir = Join-Path $ProjectRoot "src" "cofounder_agent"

# Verify directory exists
if (-not (Test-Path $CofoundeDir)) {
    Write-Host "❌ Error: cofounder_agent directory not found at $CofoundeDir" -ForegroundColor $colors["Error"]
    exit 1
}

Push-Location $CofoundeDir

try {
    # Verify pytest is available
    if (-not (Test-Command pytest)) {
        Write-Host "❌ Error: pytest not found. Install with: pip install pytest" -ForegroundColor $colors["Error"]
        exit 1
    }

    switch ($Mode) {
        "full" {
            Write-SectionHeader "Running Full Validation Suite"
            Write-Host "This runs all 32+ tests covering edge cases, pipeline workflow, and performance"
            Write-Host ""
            
            $allPassed = $true
            
            $allPassed = (Run-TestSuite "TestSystemHealth" "System Health" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestBasicTaskCreation" "Basic Functionality" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestEdgeCases" "Edge Cases" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestContentPipeline" "Content Pipeline Workflow" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestPostCreation" "Post Creation" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestErrorHandling" "Error Handling" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestPerformance" "Performance" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            $allPassed = (Run-TestSuite "TestIntegration" "Integration" $CofoundeDir) -and $allPassed
            Write-Host ""
            
            Write-Host "Running all tests with coverage report..." -ForegroundColor $colors["Warning"]
            $output = & python -m pytest tests/test_content_pipeline_comprehensive.py -v --cov=. --cov-report=term --cov-report=html 2>&1
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Full test suite with coverage completed" -ForegroundColor $colors["Success"]
                Write-Host ""
                Write-Host "Coverage report generated: htmlcov/index.html" -ForegroundColor $colors["Info"]
            } else {
                Write-Host "❌ Test suite execution failed" -ForegroundColor $colors["Error"]
                Write-Host $output
                exit 1
            }
            
            if ($allPassed) {
                Write-Host "✅ All test classes passed" -ForegroundColor $colors["Success"]
            } else {
                Write-Host "❌ Some test classes failed" -ForegroundColor $colors["Error"]
                exit 1
            }
        }
        
        "quick" {
            Write-SectionHeader "Running Quick Smoke Test"
            Write-Host "This runs only critical health and basic functionality tests (~2 minutes)"
            Write-Host ""
            
            Write-Host "Running quick validation tests..." -ForegroundColor $colors["Warning"]
            $output = & python -m pytest `
                "tests/test_content_pipeline_comprehensive.py::TestSystemHealth" `
                "tests/test_content_pipeline_comprehensive.py::TestBasicTaskCreation::test_create_task_with_minimal_fields" `
                -v --tb=short 2>&1
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Quick validation tests passed" -ForegroundColor $colors["Success"]
            } else {
                Write-Host "❌ Quick validation tests failed" -ForegroundColor $colors["Error"]
                Write-Host $output
                exit 1
            }
        }
        
        "edge-cases" {
            Write-SectionHeader "Running Edge Case Tests"
            Write-Host "Tests unicode, long strings, special characters, boundary conditions"
            Write-Host ""
            
            Write-Host "Running edge case tests..." -ForegroundColor $colors["Warning"]
            $output = & python -m pytest tests/test_content_pipeline_comprehensive.py::TestEdgeCases -v --tb=short 2>&1
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Edge case tests passed" -ForegroundColor $colors["Success"]
            } else {
                Write-Host "❌ Edge case tests failed" -ForegroundColor $colors["Error"]
                Write-Host $output
                exit 1
            }
        }
        
        "performance" {
            Write-SectionHeader "Running Performance Baseline Tests"
            Write-Host "Tests concurrent operations, large datasets, response times"
            Write-Host ""
            
            Write-Host "Running performance tests..." -ForegroundColor $colors["Warning"]
            $output = & python -m pytest tests/test_content_pipeline_comprehensive.py::TestPerformance -v --tb=short 2>&1
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Performance tests passed" -ForegroundColor $colors["Success"]
            } else {
                Write-Host "❌ Performance tests failed" -ForegroundColor $colors["Error"]
                Write-Host $output
                exit 1
            }
            
            Write-Host ""
            Write-Host "Testing API response times manually..." -ForegroundColor $colors["Warning"]
            $pythonCode = @"
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app
import time
import statistics

client = TestClient(app)

print("📊 API Response Time Baselines:\n")

# Task creation
times = []
for i in range(5):
    start = time.time()
    response = client.post('/api/tasks', json={
        'task_name': f'Performance Test {i}',
        'topic': 'AI Trends',
        'primary_keyword': 'ai'
    })
    end = time.time()
    times.append((end-start)*1000)

print(f"Task Creation:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

# List tasks
times = []
for i in range(5):
    start = time.time()
    response = client.get('/api/tasks?skip=0&limit=20')
    end = time.time()
    times.append((end-start)*1000)

print(f"\nList Tasks:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

# Get health
times = []
for i in range(10):
    start = time.time()
    response = client.get('/api/health')
    end = time.time()
    times.append((end-start)*1000)

print(f"\nHealth Check:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

print("\n✅ Performance baseline collected")
"@
            $pythonCode | python 2>&1
        }
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-SectionHeader "Validation Complete"
Write-Host "✅ All selected tests passed successfully" -ForegroundColor $colors["Success"]
Write-Host ""
Write-Host "Next steps:" -ForegroundColor $colors["Info"]
Write-Host "  1. Review test results above" -ForegroundColor $colors["Info"]
Write-Host "  2. Check htmlcov/index.html for coverage report (if using 'full')" -ForegroundColor $colors["Info"]
Write-Host "  3. Update Oversight Hub components to use new API client" -ForegroundColor $colors["Info"]
Write-Host "  4. Deploy to staging environment" -ForegroundColor $colors["Info"]
Write-Host ""
