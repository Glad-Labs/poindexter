# Kill All GLAD Labs Services
# Stops all running services on their designated ports

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host " Stopping GLAD Labs Services" -ForegroundColor Yellow  
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

$services = @(
    @{Port = 8000; Name = "AI Co-Founder API"}
    @{Port = 3001; Name = "Oversight Hub"}
    @{Port = 3000; Name = "Public Site"}
    @{Port = 1337; Name = "Strapi CMS"}
)

$stopped = 0

foreach ($service in $services) {
    Write-Host "Checking port $($service.Port) - $($service.Name)..." -ForegroundColor Cyan
    
    try {
        $conn = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
        
        if ($conn) {
            $processId = $conn.OwningProcess
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            
            if ($proc) {
                Write-Host "  Found: $($proc.ProcessName) (PID: $processId)" -ForegroundColor Yellow
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 300
                Write-Host "  Stopped" -ForegroundColor Green
                $stopped++
            }
        } else {
            Write-Host "  Port is free" -ForegroundColor Gray
        }
    } catch {
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Cleanup Complete" -ForegroundColor Green
Write-Host " Stopped: $stopped process(es)" -ForegroundColor Green  
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "You can now run: npm run dev" -ForegroundColor Cyan
Write-Host ""
