$ErrorActionPreference = "Continue"

Write-Host "OVERSIGHT HUB CONNECTION DIAGNOSTIC`n" -ForegroundColor Cyan

# Test 1: Port 8000 listening
Write-Host "1. Checking if Backend (port 8000) is listening..." -ForegroundColor Yellow
$test8000 = netstat -ano | Select-String "8000.*LISTENING"
if ($test8000) {
    Write-Host "   OK: Port 8000 is listening" -ForegroundColor Green
} else {
    Write-Host "   ERROR: Port 8000 is NOT listening" -ForegroundColor Red
}

# Test 2: Port 3001 listening
Write-Host "`n2. Checking if Oversight Hub (port 3001) is listening..." -ForegroundColor Yellow
$test3001 = netstat -ano | Select-String "3001.*LISTENING"
if ($test3001) {
    Write-Host "   OK: Port 3001 is listening" -ForegroundColor Green
} else {
    Write-Host "   ERROR: Port 3001 is NOT listening" -ForegroundColor Red
}

# Test 3: Backend health endpoint
Write-Host "`n3. Testing backend health endpoint..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -Method Get -UseBasicParsing -TimeoutSec 3
    Write-Host "   OK: Backend is responding (HTTP $($response.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "   ERROR: Backend not responding" -ForegroundColor Red
    Write-Host "   Details: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 4: Environment variable
Write-Host "`n4. Checking Oversight Hub .env.local..." -ForegroundColor Yellow
$envPath = "c:\Users\mattm\glad-labs-website\web\oversight-hub\.env.local"
if (Test-Path $envPath) {
    $apiUrl = (Get-Content $envPath | Select-String "REACT_APP_API_URL").ToString()
    Write-Host "   Found: $apiUrl" -ForegroundColor Green
} else {
    Write-Host "   ERROR: .env.local not found" -ForegroundColor Red
}

# Test 5: Check if Python processes running
Write-Host "`n5. Checking Python processes..." -ForegroundColor Yellow
$pythonProcs = @(Get-Process -Name python -ErrorAction SilentlyContinue)
Write-Host "   Found $($pythonProcs.Count) Python processes" -ForegroundColor Cyan

# Test 6: TCP connection test
Write-Host "`n6. Testing TCP connection 127.0.0.1:8000..." -ForegroundColor Yellow
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $async = $tcp.ConnectAsync("127.0.0.1", 8000)
    $async.Wait(2000) | Out-Null
    if ($tcp.Connected) {
        Write-Host "   OK: TCP connection successful" -ForegroundColor Green
        $tcp.Close()
    } else {
        Write-Host "   ERROR: TCP connection timeout" -ForegroundColor Red
    }
} catch {
    Write-Host "   ERROR: TCP connection failed" -ForegroundColor Red
}

Write-Host "`nDiagnostic complete.`n" -ForegroundColor Cyan
