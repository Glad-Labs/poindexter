# Kill All Glad Labs Services + Cleanup Zombie/Stale Processes
#
# Usage:
#   .\scripts\kill-services.ps1            # Kill services + zombies (with confirmation)
#   .\scripts\kill-services.ps1 -DryRun    # Show what would be killed without killing
#   .\scripts\kill-services.ps1 -Force     # Skip confirmation prompt
#   .\scripts\kill-services.ps1 -MemLimit 2048  # Flag python workers using > 2GB as memory hogs

param(
    [switch]$DryRun,
    [switch]$Force,
    [int]$MemLimit = 4096  # MB threshold for "memory hog" detection
)

$ErrorActionPreference = "SilentlyContinue"

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host " Glad Labs Service Killer" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
if ($DryRun) { Write-Host " [DRY RUN - no processes will be killed]" -ForegroundColor Cyan }
Write-Host ""

# ── 1. Identify active service processes by port ───────────────────────

$servicePorts = @(
    @{Port = 8000; Name = "Backend API (uvicorn)"}
    @{Port = 3001; Name = "Oversight Hub (Vite)"}
    @{Port = 3000; Name = "Public Site (Next.js)"}
)

$serviceKill = @()

foreach ($svc in $servicePorts) {
    $conns = Get-NetTCPConnection -LocalPort $svc.Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            if ($proc -and ($serviceKill -notcontains $proc.Id)) {
                $mb = [math]::Round($proc.WorkingSet64 / 1MB)
                Write-Host "  [SERVICE] $($svc.Name) - PID $($proc.Id) ($($proc.ProcessName), $mb MB)" -ForegroundColor Yellow
                $serviceKill += $proc.Id
            }
        }
    } else {
        Write-Host "  [SERVICE] $($svc.Name) - not running" -ForegroundColor DarkGray
    }
}

# ── 2. Find zombie/stale python processes (uvicorn workers, old restarts) ─

Write-Host ""
Write-Host "Scanning for zombie/stale processes..." -ForegroundColor Cyan

$pythonProcs = Get-Process python -ErrorAction SilentlyContinue
$staleKill = @()
$memHogKill = @()

foreach ($p in $pythonProcs) {
    $mb = [math]::Round($p.WorkingSet64 / 1MB)
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).CommandLine
    if (-not $cmd) { continue }

    # Skip IDE language servers, MCP servers, pgAdmin — these are not ours
    if ($cmd -match "lsp_server|mcp-server|pgAdmin|pyright|isort|black-formatter|mypy") { continue }

    $isOurs = $cmd -match "uvicorn|multiprocessing\.spawn|cofounder_agent"
    if (-not $isOurs) { continue }

    $isActiveService = $serviceKill -contains $p.Id

    # Stale: uvicorn-related process that isn't bound to a port anymore
    if (-not $isActiveService) {
        Write-Host "  [STALE]  python PID $($p.Id) - $mb MB - started $($p.StartTime.ToString('HH:mm:ss'))" -ForegroundColor Magenta
        $preview = $cmd.Substring(0, [Math]::Min(100, $cmd.Length))
        Write-Host "           $preview..." -ForegroundColor DarkGray
        $staleKill += $p.Id
    }

    # Memory hog: any uvicorn-related process over threshold
    if ($mb -gt $MemLimit) {
        if ($memHogKill -notcontains $p.Id) {
            Write-Host "  [MEMHOG] python PID $($p.Id) - $mb MB (> $MemLimit MB limit)" -ForegroundColor Red
            $memHogKill += $p.Id
        }
    }
}

# ── 3. Find orphaned node processes (old Vite/Next.js from previous sessions) ─

$nodeProcs = Get-Process node -ErrorAction SilentlyContinue
$staleNodeKill = @()

foreach ($p in $nodeProcs) {
    $mb = [math]::Round($p.WorkingSet64 / 1MB)
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).CommandLine
    if (-not $cmd) { continue }

    # Skip non-service node processes (Claude Code, MCP, Playwright, VS Code)
    if ($cmd -match "claude|mcp-server|playwright|@anthropic|extensionHost|watcherService") { continue }

    $isActiveService = $serviceKill -contains $p.Id

    if (-not $isActiveService -and ($cmd -match "next|vite|oversight|public-site|concurrently")) {
        Write-Host "  [STALE]  node PID $($p.Id) - $mb MB" -ForegroundColor Magenta
        $preview = $cmd.Substring(0, [Math]::Min(100, $cmd.Length))
        Write-Host "           $preview..." -ForegroundColor DarkGray
        $staleNodeKill += $p.Id
    }
}

# ── 4. Build combined kill list ────────────────────────────────────────

$allKill = ($serviceKill + $staleKill + $memHogKill + $staleNodeKill) | Sort-Object -Unique

$totalMB = 0
foreach ($id in $allKill) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { $totalMB += [math]::Round($p.WorkingSet64 / 1MB) }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Kill Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Service processes:  $($serviceKill.Count)" -ForegroundColor Yellow
Write-Host "  Stale python:       $($staleKill.Count)" -ForegroundColor Magenta
Write-Host "  Memory hogs:        $($memHogKill.Count)" -ForegroundColor Red
Write-Host "  Stale node:         $($staleNodeKill.Count)" -ForegroundColor Magenta
Write-Host "  Total to kill:      $($allKill.Count) processes (~$totalMB MB)" -ForegroundColor White
Write-Host ""

if ($allKill.Count -eq 0) {
    Write-Host "Nothing to kill. All clean!" -ForegroundColor Green
    Write-Host ""
    exit 0
}

if ($DryRun) {
    Write-Host "[DRY RUN] Would kill PIDs: $($allKill -join ', ')" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

if (-not $Force) {
    $confirm = Read-Host "Kill $($allKill.Count) processes (~$totalMB MB)? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 0
    }
}

# ── 5. Kill processes (biggest memory hogs first) ──────────────────────

$killed = 0
$freed = 0

# Sort by memory descending to free the biggest hogs first
$sortedKill = $allKill | ForEach-Object {
    $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
    if ($p) { [PSCustomObject]@{Id=$_; MB=[math]::Round($p.WorkingSet64/1MB); Name=$p.ProcessName} }
} | Sort-Object MB -Descending

foreach ($item in $sortedKill) {
    $p = Get-Process -Id $item.Id -ErrorAction SilentlyContinue
    if ($p) {
        try {
            $p.Kill()
            $p.WaitForExit(3000) | Out-Null
            Write-Host "  Killed PID $($item.Id) ($($item.Name), $($item.MB) MB)" -ForegroundColor Green
            $killed++
            $freed += $item.MB
        } catch {
            Write-Host "  Failed PID $($item.Id): $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

# ── 6. Verify cleanup ─────────────────────────────────────────────────

Start-Sleep -Seconds 1

$remainingPython = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    $cmd -match "uvicorn|multiprocessing|cofounder"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Done: $killed killed, ~$freed MB freed" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

if ($remainingPython) {
    Write-Host ""
    Write-Host "WARNING: $($remainingPython.Count) uvicorn-related python process(es) still running:" -ForegroundColor Red
    foreach ($p in $remainingPython) {
        $mb = [math]::Round($p.WorkingSet64 / 1MB)
        Write-Host "  PID $($p.Id) - $mb MB" -ForegroundColor Red
    }
    Write-Host "  Try running again with -Force, or kill manually: Stop-Process -Id <PID> -Force" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Restart services with: npm run dev" -ForegroundColor Cyan
Write-Host ""
