# Test Oversight Hub connection to backend

Write-Host "`nOVERSIGHT HUB CONNECTION TEST`n" -ForegroundColor Cyan

# Step 1: Check environment
Write-Host "1. Checking Oversight Hub configuration..." -ForegroundColor Yellow
$envPath = "c:\Users\mattm\glad-labs-website\web\oversight-hub\.env.local"
$apiUrl = (Get-Content $envPath | Select-String "^REACT_APP_API_URL" | Select-Object -First 1)
Write-Host "   API URL: $($apiUrl.ToString().Split('=')[1])" -ForegroundColor Cyan

# Step 2: Check Oversight Hub port
Write-Host "`n2. Checking if Oversight Hub (port 3001) is running..." -ForegroundColor Yellow
$ohub = netstat -ano | Select-String "3001.*LISTENING"
if ($ohub) {
    Write-Host "   OK - Oversight Hub is listening on port 3001" -ForegroundColor Green
} else {
    Write-Host "   ERROR - Oversight Hub is NOT listening" -ForegroundColor Red
}

# Step 3: Check backend port
Write-Host "`n3. Checking if Backend (port 8000) is running..." -ForegroundColor Yellow
$backend = netstat -ano | Select-String "8000.*LISTENING"
if ($backend) {
    Write-Host "   OK - Backend is listening on port 8000" -ForegroundColor Green
} else {
    Write-Host "   ERROR - Backend is NOT listening" -ForegroundColor Red
}

# Step 4: Test backend health
Write-Host "`n4. Testing backend health endpoint..." -ForegroundColor Yellow
try {
    $response = curl http://127.0.0.1:8000/api/health -UseBasicParsing 2>$null
    if ($response.StatusCode -eq 200) {
        Write-Host "   OK - Backend is healthy" -ForegroundColor Green
        $body = $response.Content | ConvertFrom-Json
        Write-Host "      Status: $($body.status)" -ForegroundColor Green
        Write-Host "      Database: $($body.components.database)" -ForegroundColor Green
    }
} catch {
    Write-Host "   ERROR - Backend not responding" -ForegroundColor Red
}

# Step 5: Test from browser perspective
Write-Host "`n5. Testing from Oversight Hub's perspective..." -ForegroundColor Yellow
Write-Host "   Simulating browser request to http://127.0.0.1:8000/api/health" -ForegroundColor Gray
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "   SUCCESS - Browser can connect" -ForegroundColor Green
    }
} catch {
    Write-Host "   ERROR - Browser connection failed: $($_.Exception.Message)" -ForegroundColor Red
}

# Step 6: CORS headers check
Write-Host "`n6. Checking CORS headers..." -ForegroundColor Yellow
try {
    $headers = @{
        "Origin" = "http://localhost:3001"
    }
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -Headers $headers -TimeoutSec 5
    $corsHeader = $response.Headers["Access-Control-Allow-Origin"]
    if ($corsHeader) {
        Write-Host "   OK - CORS allows: $corsHeader" -ForegroundColor Green
    } else {
        Write-Host "   WARNING - No CORS header in response" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   Error checking CORS: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n===== SUMMARY =====" -ForegroundColor Cyan
Write-Host "If all checks passed, Oversight Hub should be able to connect!" -ForegroundColor Green
Write-Host "Try refreshing the browser (Ctrl+Shift+R) to clear cache`n" -ForegroundColor Gray
