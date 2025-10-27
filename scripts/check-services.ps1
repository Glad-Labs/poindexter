# Check Status of All Glad Labs Services
# Displays the status of all services on their designated ports

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Glad Labs Services Status" -ForegroundColor Cyan  
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$services = @(
    @{Port = 8000; Name = "AI Co-Founder API"; URL = "http://localhost:8000/docs"}
    @{Port = 3001; Name = "Oversight Hub"; URL = "http://localhost:3001"}
    @{Port = 3000; Name = "Public Site"; URL = "http://localhost:3000"}
    @{Port = 1337; Name = "Strapi CMS"; URL = "http://localhost:1337/admin"}
)

$running = 0

foreach ($service in $services) {
    Write-Host "$($service.Name) (Port $($service.Port))" -ForegroundColor Yellow
    Write-Host "  URL: $($service.URL)" -ForegroundColor Gray
    
    try {
        $conn = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
        
        if ($conn) {
            $processId = $conn.OwningProcess
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            
            if ($proc) {
                $startTime = $proc.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
                $uptime = (Get-Date) - $proc.StartTime
                $uptimeStr = "{0:D2}h {1:D2}m {2:D2}s" -f $uptime.Hours, $uptime.Minutes, $uptime.Seconds
                
                Write-Host "  Status: RUNNING" -ForegroundColor Green
                Write-Host "  Process: $($proc.ProcessName) (PID: $processId)" -ForegroundColor Gray
                Write-Host "  Started: $startTime" -ForegroundColor Gray
                Write-Host "  Uptime: $uptimeStr" -ForegroundColor Gray
                $running++
            }
        } else {
            Write-Host "  Status: NOT RUNNING" -ForegroundColor Red
        }
    } catch {
        Write-Host "  Status: ERROR - $($_.Exception.Message)" -ForegroundColor Red
    }
    
    Write-Host ""
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Summary: $running of $($services.Count) services running" -ForegroundColor $(if ($running -eq $services.Count) { "Green" } else { "Yellow" })
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($running -eq 0) {
    Write-Host "No services running. Start with: npm run dev" -ForegroundColor Yellow
} elseif ($running -lt $services.Count) {
    Write-Host "Some services are not running. Restart with: npm run services:restart" -ForegroundColor Yellow
} else {
    Write-Host "All services are running!" -ForegroundColor Green
}

Write-Host ""
