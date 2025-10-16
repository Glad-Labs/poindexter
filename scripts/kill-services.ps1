# Kill All GLAD Labs Services
# This script stops all running services on their designated ports

Write-Host "`n🛑 Stopping all GLAD Labs services..." -ForegroundColor Yellow
Write-Host "=" * 60

# Define ports and service names
$services = @(
    @{Port = 8000; Name = "AI Co-Founder API (FastAPI)"}
    @{Port = 3001; Name = "Oversight Hub (React)"}
    @{Port = 3000; Name = "Public Site (Next.js)"}
    @{Port = 1337; Name = "Strapi CMS"}
)

$stoppedCount = 0

foreach ($service in $services) {
    Write-Host "`nChecking port $($service.Port) - $($service.Name)..." -ForegroundColor Cyan
    
    try {
        # Find process using this port
        $connection = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
        
        if ($connection) {
            $processId = $connection.OwningProcess
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            
            if ($process) {
                Write-Host "  ├─ Found: $($process.ProcessName) (PID: $processId)" -ForegroundColor Yellow
                Write-Host "  ├─ Stopping process..." -ForegroundColor Yellow
                
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 500
                
                # Verify it stopped
                $stillRunning = Get-Process -Id $processId -ErrorAction SilentlyContinue
                if (-not $stillRunning) {
                    Write-Host "  └─ ✅ Stopped successfully" -ForegroundColor Green
                    $stoppedCount++
                } else {
                    Write-Host "  └─ ⚠️  Process still running" -ForegroundColor Red
                }
            }
        } else {
            Write-Host "  └─ ✅ Port is free" -ForegroundColor Green
        }
    } catch {
        Write-Host "  └─ ❌ Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Also kill any Python processes that might be lingering
Write-Host "`nChecking for lingering Python processes..." -ForegroundColor Cyan
$pythonProcesses = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "*glad-labs*" -or $_.MainWindowTitle -like "*Co-Founder*"
}

if ($pythonProcesses) {
    foreach ($proc in $pythonProcesses) {
        Write-Host "  ├─ Found Python process: PID $($proc.Id)" -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  └─ ✅ Stopped" -ForegroundColor Green
        $stoppedCount++
    }
}

# Also kill any Node processes that might be lingering
Write-Host "`nChecking for lingering Node processes..." -ForegroundColor Cyan
$nodeProcesses = Get-Process -Name node -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*glad-labs*" -or $_.CommandLine -like "*strapi*" -or $_.CommandLine -like "*next*"
}

if ($nodeProcesses) {
    foreach ($proc in $nodeProcesses) {
        Write-Host "  ├─ Found Node process: PID $($proc.Id)" -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  └─ ✅ Stopped" -ForegroundColor Green
        $stoppedCount++
    }
}

Write-Host "`n" + "=" * 60
Write-Host "✅ Cleanup complete! Stopped $stoppedCount process(es)." -ForegroundColor Green
Write-Host "`n💡 You can now run: npm run dev" -ForegroundColor Cyan
Write-Host ""
