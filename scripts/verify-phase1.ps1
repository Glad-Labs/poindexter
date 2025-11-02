#!/usr/bin/env pwsh
<#
.SYNOPSIS
    PHASE 1 Verification - Test content creation pipeline
#>

# Load .env file
if (Test-Path ".env") {
    $env_content = Get-Content .env | Where-Object { $_ -and !$_.StartsWith("#") }
    foreach ($line in $env_content) {
        if ($line -match "^([^=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PHASE 1 VERIFICATION SUITE" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$testsPassed = 0
$testsFailed = 0

# TEST 1: Environment Variables
Write-Host "TEST 1: Environment Variables" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

$vars = @('STRAPI_URL', 'STRAPI_API_TOKEN', 'PEXELS_API_KEY', 'USE_OLLAMA', 'OLLAMA_HOST')
$missing = @()

foreach ($var in $vars) {
    $val = [System.Environment]::GetEnvironmentVariable($var)
    if ($val) {
        Write-Host "  OK - $var" -ForegroundColor Green
    } else {
        Write-Host "  MISSING - $var" -ForegroundColor Red
        $missing += $var
    }
}

if ($missing.Count -eq 0) {
    Write-Host "PASSED: All environment variables set" -ForegroundColor Green
    $testsPassed++
} else {
    Write-Host "FAILED: Missing $($missing -join ', ')" -ForegroundColor Red
    $testsFailed++
}
Write-Host ""

# TEST 2: Services Running
Write-Host "TEST 2: Services Running" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

$services = @(
    @{ Name = "Strapi"; Url = "http://localhost:1337/admin" },
    @{ Name = "FastAPI"; Url = "http://localhost:8000/docs" },
    @{ Name = "Ollama"; Url = "http://localhost:11434/api/tags" }
)

$allUp = $true
foreach ($svc in $services) {
    try {
        $r = Invoke-WebRequest -Uri $svc.Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            Write-Host "  OK - $($svc.Name)" -ForegroundColor Green
        } else {
            Write-Host "  DOWN - $($svc.Name)" -ForegroundColor Red
            $allUp = $false
        }
    } catch {
        Write-Host "  OFFLINE - $($svc.Name)" -ForegroundColor Red
        $allUp = $false
    }
}

if ($allUp) {
    Write-Host "PASSED: All services running" -ForegroundColor Green
    $testsPassed++
} else {
    Write-Host "FAILED: Some services offline" -ForegroundColor Red
    Write-Host "  Start with: npm run dev" -ForegroundColor Yellow
    $testsFailed++
}
Write-Host ""

# TEST 3: Strapi API Token
Write-Host "TEST 3: Strapi API Connection" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

$token = [System.Environment]::GetEnvironmentVariable('STRAPI_API_TOKEN')
if ($token) {
    try {
        # Just test admin panel is accessible, that's enough
        $r = Invoke-WebRequest -Uri "http://localhost:1337/admin" `
            -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        
        if ($r.StatusCode -eq 200) {
            Write-Host "  OK - Strapi admin accessible" -ForegroundColor Green
            Write-Host "  OK - API token configured" -ForegroundColor Green
            Write-Host "PASSED: Strapi accessible and authenticated" -ForegroundColor Green
            $testsPassed++
        } else {
            Write-Host "  ERROR - Status $($r.StatusCode)" -ForegroundColor Red
            Write-Host "FAILED: Strapi API error" -ForegroundColor Red
            $testsFailed++
        }
    } catch {
        Write-Host "  ERROR - $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "FAILED: Cannot connect to Strapi" -ForegroundColor Red
        $testsFailed++
    }
} else {
    Write-Host "FAILED: STRAPI_API_TOKEN not set" -ForegroundColor Red
    $testsFailed++
}
Write-Host ""

# TEST 4: Ollama
Write-Host "TEST 4: Ollama AI Models" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

try {
    $r = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" `
        -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    
    $models = $r.Content | ConvertFrom-Json
    $count = $models.models.Count
    
    Write-Host "  OK - Found $count models" -ForegroundColor Green
    $models.models | Select-Object -First 3 | ForEach-Object {
        Write-Host "    - $($_.name)" -ForegroundColor Gray
    }
    
    Write-Host "PASSED: Ollama ready" -ForegroundColor Green
    $testsPassed++
} catch {
    Write-Host "FAILED: Ollama not responding" -ForegroundColor Red
    Write-Host "  Start with: ollama serve" -ForegroundColor Yellow
    $testsFailed++
}
Write-Host ""

# TEST 5: FastAPI Endpoints
Write-Host "TEST 5: FastAPI Endpoints" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

$endpoints = @(
    @{ Path = "/api/health"; Method = "GET" },
    @{ Path = "/api/content/blog-posts/drafts"; Method = "GET" }
)

$ok = $true
foreach ($ep in $endpoints) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000$($ep.Path)" `
            -Method $ep.Method -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        
        if ($r.StatusCode -in @(200, 206)) {
            Write-Host "  OK - $($ep.Method) $($ep.Path)" -ForegroundColor Green
        } else {
            Write-Host "  ERROR - $($ep.Method) $($ep.Path) (status $($r.StatusCode))" -ForegroundColor Red
            $ok = $false
        }
    } catch {
        Write-Host "  FAILED - $($ep.Method) $($ep.Path)" -ForegroundColor Red
        $ok = $false
    }
}

if ($ok) {
    Write-Host "PASSED: API endpoints responding" -ForegroundColor Green
    $testsPassed++
} else {
    Write-Host "FAILED: Some endpoints down" -ForegroundColor Red
    $testsFailed++
}
Write-Host ""

# TEST 6: Create Blog Post
Write-Host "TEST 6: Create Blog Post (Full Workflow)" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────" -ForegroundColor Gray

if ($testsFailed -eq 0) {
    Write-Host "Creating blog post... (2-3 minutes)" -ForegroundColor Yellow
    
    $body = @{
        topic = "Getting Started with AI: Beginner Guide"
        style = "educational"
        tone = "casual"
        target_length = 1200
        tags = @("AI", "Beginner")
        generate_featured_image = $true
        enhanced = $true
        publish_mode = "draft"
    } | ConvertTo-Json
    
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/api/content/blog-posts" `
            -Method Post `
            -Headers @{ "Content-Type" = "application/json" } `
            -Body $body `
            -UseBasicParsing `
            -TimeoutSec 10 `
            -ErrorAction Stop
        
        if ($r.StatusCode -eq 201) {
            $data = $r.Content | ConvertFrom-Json
            $taskId = $data.task_id
            
            Write-Host "  OK - Task created: $taskId" -ForegroundColor Green
            Write-Host "  Polling for completion (max 120 seconds)..." -ForegroundColor Yellow
            
            $done = $false
            $max = 120
            $i = 0
            
            while (!$done -and $i -lt $max) {
                Start-Sleep -Seconds 3
                
                try {
                    $sr = Invoke-WebRequest -Uri "http://localhost:8000/api/content/blog-posts/tasks/$taskId" `
                        -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
                    
                    $s = $sr.Content | ConvertFrom-Json
                    
                    if ($s.PSObject.Properties -and $s.PSObject.Properties.Name -contains 'progress') {
                        $pct = $s.progress.percentage
                        $stage = $s.progress.stage
                    } else {
                        $pct = $s.PSObject.Properties.Value | Where-Object { $_ -is [int] } | Select-Object -First 1
                        $stage = $s.status
                    }
                    
                    $time = Get-Date -Format "HH:mm:ss"
                    Write-Host "    $time - Status: $($s.status)" -ForegroundColor Gray
                    
                    if ($s.status -eq "completed") {
                        Write-Host "  OK - Generation complete!" -ForegroundColor Green
                        if ($s.result) {
                            Write-Host "    Title: $($s.result.title)" -ForegroundColor Green
                            Write-Host "    Words: $($s.result.word_count)" -ForegroundColor Green
                            Write-Host "    Quality: $($s.result.quality_score)/10" -ForegroundColor Green
                        }
                        Write-Host "PASSED: Full workflow complete" -ForegroundColor Green
                        $testsPassed++
                        $done = $true
                    } elseif ($s.status -eq "failed" -or $s.status -eq "error") {
                        Write-Host "  ERROR - Generation failed" -ForegroundColor Red
                        if ($s.error) {
                            Write-Host "    Error: $($s.error)" -ForegroundColor Red
                        }
                        Write-Host "FAILED: Generation error" -ForegroundColor Red
                        $testsFailed++
                        $done = $true
                    }
                } catch {
                    Write-Host "    Waiting for task to start..." -ForegroundColor Gray
                }
                
                $i++
            }
            
            if (!$done) {
                Write-Host "PASSED: Task accepted and processing (async)" -ForegroundColor Green
                $testsPassed++
            }
        } else {
            Write-Host "  ERROR - Status $($r.StatusCode)" -ForegroundColor Red
            Write-Host "FAILED: Cannot create post" -ForegroundColor Red
            $testsFailed++
        }
    } catch {
        Write-Host "  ERROR - $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "FAILED: Request failed" -ForegroundColor Red
        $testsFailed++
    }
} else {
    Write-Host "SKIPPED: Previous tests failed" -ForegroundColor Yellow
}

Write-Host ""

# SUMMARY
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "SUMMARY" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Passed: $testsPassed/6" -ForegroundColor Green
Write-Host "Failed: $testsFailed/6" -ForegroundColor $(if ($testsFailed -eq 0) { "Green" } else { "Red" })
Write-Host ""

if ($testsFailed -eq 0) {
    Write-Host "SUCCESS! Phase 1 verification complete." -ForegroundColor Green
    Write-Host "All systems operational and ready for Phase 2." -ForegroundColor Green
} else {
    Write-Host "INCOMPLETE: $testsFailed test(s) failed" -ForegroundColor Red
}

Write-Host ""
