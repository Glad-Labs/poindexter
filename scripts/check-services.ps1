# Check Status of GLAD Labs Services
# This script checks which services are running and on which ports

Write-Host "`n🔍 Checking GLAD Labs service status..." -ForegroundColor Cyan
Write-Host "=" * 80

# Define expected services
$services = @(
    @{Port = 8000; Name = "AI Co-Founder API (FastAPI)"; URL = "http://localhost:8000/health"}
    @{Port = 3001; Name = "Oversight Hub (React)"; URL = "http://localhost:3001"}
    @{Port = 3000; Name = "Public Site (Next.js)"; URL = "http://localhost:3000"}
    @{Port = 1337; Name = "Strapi CMS"; URL = "http://localhost:1337/admin"}
)

$runningCount = 0

foreach ($service in $services) {
    Write-Host "`n📍 Port $($service.Port) - $($service.Name)" -ForegroundColor Yellow
    Write-Host "   URL: $($service.URL)" -ForegroundColor DarkGray
    
    try {
        $connection = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
        
        if ($connection) {
            $processId = $connection.OwningProcess
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            
            if ($process) {
                $startTime = $process.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
                $runningTime = (Get-Date) - $process.StartTime
                $runningStr = "{0:D2}h {1:D2}m {2:D2}s" -f $runningTime.Hours, $runningTime.Minutes, $runningTime.Seconds
                
                Write-Host "   ✅ RUNNING" -ForegroundColor Green
                Write-Host "      Process: $($process.ProcessName) (PID: $processId)" -ForegroundColor Gray
                Write-Host "      Started: $startTime" -ForegroundColor Gray
                Write-Host "      Uptime:  $runningStr" -ForegroundColor Gray
                $runningCount++
            }
        } else {
            Write-Host "   ❌ NOT RUNNING" -ForegroundColor Red
        }
    } catch {
        Write-Host "   ❌ ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Check for any other processes on common ports
Write-Host "`n🔎 Checking for other processes on development ports..." -ForegroundColor Cyan
$otherPorts = 5000, 5001, 5173, 8080, 8888, 9000
foreach ($port in $otherPorts) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "   ⚠️  Port $port in use by $($proc.ProcessName) (PID: $($conn.OwningProcess))" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n" + "=" * 80
Write-Host "📊 Summary: $runningCount out of $($services.Count) services are running" -ForegroundColor $(if ($runningCount -eq $services.Count) { "Green" } elseif ($runningCount -gt 0) { "Yellow" } else { "Red" })

if ($runningCount -eq $services.Count) {
    Write-Host "✅ All services are running!" -ForegroundColor Green
} elseif ($runningCount -gt 0) {
    Write-Host "⚠️  Some services are not running. Use 'npm run dev' to start all services." -ForegroundColor Yellow
} else {
    Write-Host "❌ No services are running. Use 'npm run dev' to start all services." -ForegroundColor Red
}

Write-Host ""
