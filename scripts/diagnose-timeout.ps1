# Timeout Diagnostics Script (PowerShell)
# Usage: .\diagnose-timeout.ps1

Write-Host "🔍 Strapi API Diagnostics" -ForegroundColor White
Write-Host "==========================" -ForegroundColor White
Write-Host ""

$STRAPI_URL = $env:NEXT_PUBLIC_STRAPI_API_URL -replace '\s+', ''
if ([string]::IsNullOrEmpty($STRAPI_URL)) {
    $STRAPI_URL = "http://localhost:1337"
}

Write-Host "📡 Testing Strapi at: $STRAPI_URL" -ForegroundColor Cyan
Write-Host ""

# Test 1: Check if server is reachable
Write-Host "1️⃣  Connection Test..." -ForegroundColor White
try {
    $response = $null
    $request = [System.Net.HttpWebRequest]::Create("$STRAPI_URL/api/health")
    $request.Method = "GET"
    $request.Timeout = 5000
    
    try {
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        
        if ($statusCode -eq 200) {
            Write-Host "✓ Strapi is reachable (HTTP $statusCode)" -ForegroundColor Green
        } elseif ($statusCode -eq 404) {
            Write-Host "⚠ Strapi reachable but endpoint not found (HTTP $statusCode)" -ForegroundColor Yellow
        } else {
            Write-Host "✗ Strapi returned HTTP $statusCode" -ForegroundColor Red
        }
    } catch [System.Net.WebException] {
        Write-Host "✗ Connection failed: $($_.Exception.Message)" -ForegroundColor Red
    } finally {
        if ($response) { $response.Close() }
    }
} catch {
    Write-Host "✗ Connection timeout - Strapi not responding" -ForegroundColor Red
}

Write-Host ""

# Test 2: Check response time
Write-Host "2️⃣  Response Time Test..." -ForegroundColor White
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $response = Invoke-WebRequest -Uri "$STRAPI_URL/api/posts?pagination[limit]=1" -Method Get -TimeoutSec 10 -ErrorAction Stop
    $stopwatch.Stop()
    $elapsed = $stopwatch.ElapsedMilliseconds
    $statusCode = $response.StatusCode
    
    if ($elapsed -lt 1000) {
        Write-Host "✓ Response time: ${elapsed}ms (HTTP $statusCode)" -ForegroundColor Green
    } elseif ($elapsed -lt 5000) {
        Write-Host "⚠ Slow response: ${elapsed}ms (threshold: 5000ms)" -ForegroundColor Yellow
    } else {
        Write-Host "✗ Very slow response: ${elapsed}ms" -ForegroundColor Red
    }
} catch {
    $stopwatch.Stop()
    Write-Host "✗ No response from API: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# Test 3: Check specific endpoints
Write-Host "3️⃣  Endpoint Tests..." -ForegroundColor White

$endpoints = @("/posts", "/categories", "/tags")

foreach ($endpoint in $endpoints) {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    
    try {
        $response = Invoke-WebRequest -Uri "$STRAPI_URL/api${endpoint}?pagination[limit]=1" -Method Get -TimeoutSec 5 -ErrorAction Stop
        $stopwatch.Stop()
        $elapsed = $stopwatch.ElapsedMilliseconds
        Write-Host "✓ $endpoint : ${elapsed}ms" -ForegroundColor Green
    } catch {
        $stopwatch.Stop()
        $elapsed = $stopwatch.ElapsedMilliseconds
        $statusCode = $null
        if ($_.Exception -is [System.Net.WebException]) {
            $statusCode = $_.Exception.Response.StatusCode
        }
        Write-Host "✗ $endpoint : HTTP $statusCode (${elapsed}ms)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "📊 Summary" -ForegroundColor White
Write-Host "==========" -ForegroundColor White
Write-Host "If you see ✓ for all tests: Your Strapi is healthy" -ForegroundColor Green
Write-Host "If you see ⚠ or ✗: Investigate Strapi logs and networking" -ForegroundColor Yellow
Write-Host ""
Write-Host "🔗 Next steps:" -ForegroundColor Cyan
Write-Host "1. Check Railway status: https://railway.app" -ForegroundColor White
Write-Host "2. Check Vercel environment variables" -ForegroundColor White
Write-Host "3. Verify Strapi deployment is running" -ForegroundColor White
Write-Host "4. Check network connectivity from Vercel to Railway" -ForegroundColor White
