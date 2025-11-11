# Diagnostic script to check Oversight Hub ↔ Co-founder Agent connectivity

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "OVERSIGHT HUB ↔ CO-FOUNDER AGENT DIAGNOSTIC" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 1. Check if backend is listening
Write-Host "1. BACKEND LISTENING PORTS" -ForegroundColor Yellow
Write-Host "   Checking port 8000..." -ForegroundColor Gray
$port8000 = netstat -ano | Select-String "8000"
if ($port8000) {
    Write-Host "   ✅ Port 8000 is LISTENING" -ForegroundColor Green
    $port8000 | Select-String "LISTENING" | ForEach-Object { Write-Host "      $_" }
} else {
    Write-Host "   ❌ Port 8000 NOT LISTENING" -ForegroundColor Red
}

# 2. Check if Oversight Hub is listening
Write-Host "`n2. OVERSIGHT HUB LISTENING PORTS" -ForegroundColor Yellow
Write-Host "   Checking port 3001..." -ForegroundColor Gray
$port3001 = netstat -ano | Select-String "3001"
if ($port3001) {
    Write-Host "   ✅ Port 3001 is LISTENING" -ForegroundColor Green
    $port3001 | Select-String "LISTENING" | Select-Object -First 1 | ForEach-Object { Write-Host "      $_" }
} else {
    Write-Host "   ❌ Port 3001 NOT LISTENING" -ForegroundColor Red
}

# 3. Test backend health on localhost
Write-Host "`n3. BACKEND HEALTH (localhost)" -ForegroundColor Yellow
Write-Host "   Testing http://127.0.0.1:8000/api/health..." -ForegroundColor Gray
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -Method Get -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Host "   ✅ Backend HEALTHY (Status: $($response.StatusCode))" -ForegroundColor Green
    $body = $response.Content | ConvertFrom-Json
    Write-Host "      Status: $($body.status)" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Backend NOT RESPONDING" -ForegroundColor Red
    Write-Host "      Error: $($_.Exception.Message)" -ForegroundColor Red
}

# 4. Get network IP addresses
Write-Host "`n4. LOCAL NETWORK CONFIGURATION" -ForegroundColor Yellow
$ipConfig = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -match "^192\.|^10\.|^172\."}
if ($ipConfig) {
    Write-Host "   Network IPs found:" -ForegroundColor Gray
    $ipConfig | ForEach-Object { 
        Write-Host "      $($_.IPAddress) ($($_.InterfaceAlias))" -ForegroundColor Cyan
    }
} else {
    Write-Host "   No private network IPs found" -ForegroundColor Gray
}

# 5. Test backend health from network IP (if available)
$networkIP = $ipConfig | Select-Object -First 1 -ExpandProperty IPAddress
if ($networkIP) {
    Write-Host "`n5. BACKEND HEALTH (network IP)" -ForegroundColor Yellow
    Write-Host "   Testing http://$($networkIP):8000/api/health..." -ForegroundColor Gray
    try {
        $response = Invoke-WebRequest -Uri "http://$($networkIP):8000/api/health" -Method Get -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        Write-Host "   ✅ Backend RESPONDS on network IP (Status: $($response.StatusCode))" -ForegroundColor Green
        $body = $response.Content | ConvertFrom-Json
        Write-Host "      Status: $($body.status)" -ForegroundColor Green
    } catch {
        Write-Host "   ⚠️  Backend NOT responding on network IP" -ForegroundColor Yellow
        Write-Host "      Error: $($_.Exception.Message.Split("`n") | Select-Object -First 1)" -ForegroundColor Yellow
    }
}

# 6. Check environment variable in Oversight Hub
Write-Host "`n6. OVERSIGHT HUB CONFIG" -ForegroundColor Yellow
$envLocalPath = "c:\Users\mattm\glad-labs-website\web\oversight-hub\.env.local"
if (Test-Path $envLocalPath) {
    Write-Host "   Found .env.local" -ForegroundColor Green
    $apiUrl = Get-Content $envLocalPath | Select-String "REACT_APP_API_URL=" | Select-Object -First 1
    if ($apiUrl) {
        Write-Host "      $($apiUrl)" -ForegroundColor Cyan
    }
} else {
    Write-Host "   ❌ .env.local NOT FOUND at $envLocalPath" -ForegroundColor Red
}

# 7. Check backend process details
Write-Host "`n7. BACKEND PROCESS INFO" -ForegroundColor Yellow
$pythonProcs = Get-Process | Where-Object {$_.ProcessName -eq "python"}
Write-Host "   Python processes running: $($pythonProcs.Count)" -ForegroundColor Gray
if ($pythonProcs) {
    $pythonProcs | ForEach-Object {
        Write-Host "      PID: $($_.Id) | Memory: $([math]::Round($_.WorkingSet/1MB,2))MB" -ForegroundColor Cyan
    }
}

# 8. Test basic connectivity between ports
Write-Host "`n8. CONNECTIVITY TEST" -ForegroundColor Yellow
Write-Host "   Attempting connection from 127.0.0.1:3001 to 127.0.0.1:8000..." -ForegroundColor Gray
try {
    $socket = New-Object System.Net.Sockets.TcpClient
    $result = $socket.ConnectAsync("127.0.0.1", 8000)
    if ($result.Wait(2000)) {
        if ($socket.Connected) {
            Write-Host "   SUCCESS: TCP Connection established" -ForegroundColor Green
            $socket.Close()
        }
    } else {
        Write-Host "   FAILED: TCP Connection timeout" -ForegroundColor Red
    }
} catch {
    Write-Host "   FAILED: TCP Connection: $($_.Exception.Message.Split("`n") | Select-Object -First 1)" -ForegroundColor Red
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "DIAGNOSIS COMPLETE" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Print recommendations
Write-Host "RECOMMENDATIONS:" -ForegroundColor Yellow
Write-Host "1. If backend is NOT responding on localhost (127.0.0.1:8000):" -ForegroundColor Gray
Write-Host "   - Restart the backend server" -ForegroundColor Gray
Write-Host "   - Check src/cofounder_agent/main.py for startup errors" -ForegroundColor Gray
Write-Host "" -ForegroundColor Gray
Write-Host "2. If backend responds on network IP but not localhost:" -ForegroundColor Gray
Write-Host "   - Backend is binding to 0.0.0.0 or specific network interface" -ForegroundColor Gray
Write-Host "   - Oversight Hub environment should be updated to use network IP" -ForegroundColor Gray
Write-Host "" -ForegroundColor Gray
Write-Host "3. If both Oversight Hub and Backend are running:" -ForegroundColor Gray
Write-Host "   - Check browser console (F12) for specific error messages" -ForegroundColor Gray
Write-Host "   - Verify CORS is properly configured in backend" -ForegroundColor Gray
Write-Host "   - Try hard-refresh in browser (Ctrl+Shift+R)" -ForegroundColor Gray
