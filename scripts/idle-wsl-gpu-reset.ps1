<#
.SYNOPSIS
    Idle-only host-side WSL/Docker GPU reset (PR 2, 2026-07-12 desktop-lockup
    fix follow-up).

.DESCRIPTION
    The Wan video render can oversubscribe the display GPU and freeze the
    desktop when WSL2's vmwp/vmmemWSL process holds stale GPU memory that no
    container restart returns to the host. Only "wsl --shutdown" plus a
    Docker Desktop restart actually frees it, and the worker runs INSIDE
    WSL, so it cannot reset the VM it is running in - this has to be a
    Windows host task.

    This script never resets blind. It measures how long the user has been
    away (GetLastInputInfo) and hands that to
    scripts/idle_wsl_gpu_reset_check.py, which is the single source of truth
    for the rest of the conditions (nothing in progress or mid-dispatch,
    retention actually high, GPU not busy, cooldown elapsed) - all
    DB-configurable via app_settings, master-switched off by default
    (idle_wsl_reset_enabled).

.PARAMETER DryRun
    Evaluate every condition and log the decision. Never resets anything.
    Safe to run any time, including while idle_wsl_reset_enabled=false.

.NOTES
    Registered as a disabled-by-default Windows Scheduled Task by
    scripts/register-idle-wsl-reset.ps1. Runbook:
    docs/operations/idle-wsl-gpu-reset.md
#>
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "src\cofounder_agent"
$checkerScript = Join-Path $repoRoot "scripts\idle_wsl_gpu_reset_check.py"
$logDir = Join-Path $env:USERPROFILE ".poindexter\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "idle-wsl-gpu-reset.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

# --- How the checker gets invoked ---------------------------------------------
# The checker is a CONTAINER-side tool, not a host tool. Both of its runtime
# dependencies resolve only on the Docker network:
#   * app_settings.gpu_metrics_prometheus_url is `http://prometheus:9090` - a
#     compose hostname the Windows host cannot resolve AT ALL. The checker reads
#     free-VRAM and GPU utilization from Prometheus, so run on the host it can
#     only ever report "Prometheus VRAM/utilization unreadable - failing closed".
#   * the DB is reached in-container at `postgres-local:5432`. From the host it
#     needs the published :5433, which is itself prone to Docker Desktop
#     port-forward wedges (observed hanging on BOTH IPv4 and IPv6, surviving a
#     full `wsl --shutdown` + Docker Desktop restart).
# So the checker could never produce a real decision from the host, independent
# of the interpreter (#883) and registrar (#882) bugs. Run it INSIDE the worker
# container, where repo-root scripts/ is already bind-mounted at /opt/scripts and
# py3.13 + asyncpg + httpx are present.
#
# Host-python stays as a FALLBACK for topologies that do point those settings at
# host-reachable addresses. If neither transport works, the checker emits no
# parseable JSON and the script exits 2 without resetting (fail closed).
$ContainerName = if ($env:POINDEXTER_IDLE_RESET_CONTAINER) {
    $env:POINDEXTER_IDLE_RESET_CONTAINER
} else {
    "poindexter-worker"
}
$ContainerCheckerPath = "/opt/scripts/idle_wsl_gpu_reset_check.py"

function Test-CheckerContainer {
    # Probed per call rather than cached: the post-reset --stamp-cooldown and
    # --notify calls happen after `wsl --shutdown` tore the container down and
    # the stack has (hopefully) come back, so the correct transport can differ
    # between the start and the end of a single run.
    try {
        $state = & docker inspect -f '{{.State.Running}}' $ContainerName 2>$null
        return ($LASTEXITCODE -eq 0 -and "$state".Trim() -eq "true")
    } catch {
        return $false
    }
}

# Host-python fallback, in preference order:
#   1. $POINDEXTER_IDLE_RESET_PYTHON - optional operator override (absolute
#      path to a python.exe); the escape hatch when neither default fits.
#   2. the repo-root .venv           - portable (resolves under wherever the
#      repo is checked out) and the interpreter the sibling host GPU tasks use.
#   3. `poetry run python`           - last resort so a box set up only via
#      poetry still runs *something*; a wrong resolve there just makes the
#      checker fail closed (no reset), never a bad reset.
function Resolve-CheckerPython {
    $override = $env:POINDEXTER_IDLE_RESET_PYTHON
    if ($override -and (Test-Path $override)) { return , @($override) }

    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { return , @($venvPython) }

    return , @("poetry", "run", "python")
}

$script:CheckerPython = Resolve-CheckerPython

function Invoke-Checker {
    param([string[]]$CheckerArgs)
    if (Test-CheckerContainer) {
        $output = & docker exec $ContainerName python $ContainerCheckerPath @CheckerArgs 2>&1
        return @{ Output = ($output -join "`n"); ExitCode = $LASTEXITCODE }
    }
    Push-Location $backendDir
    try {
        $exe = $script:CheckerPython[0]
        $prefixArgs = @()
        if ($script:CheckerPython.Count -gt 1) {
            $prefixArgs = $script:CheckerPython[1..($script:CheckerPython.Count - 1)]
        }
        $output = & $exe @prefixArgs $checkerScript @CheckerArgs 2>&1
        return @{ Output = ($output -join "`n"); ExitCode = $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

Write-Log "=== idle-wsl-gpu-reset starting (DryRun=$DryRun) ==="
if (Test-CheckerContainer) {
    Write-Log "Checker transport: docker exec $ContainerName python $ContainerCheckerPath"
} else {
    Write-Log "Checker transport: HOST FALLBACK ($($script:CheckerPython -join ' ')) - container '$ContainerName' is not running; expect a fail-closed decision unless this host can reach Prometheus + the DB directly."
}

# --- Idle time via the native Win32 GetLastInputInfo API --------------------
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class IdleWslResetIdleTime {
    [StructLayout(LayoutKind.Sequential)]
    struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")]
    static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    public static uint GetIdleMilliseconds() {
        LASTINPUTINFO lii = new LASTINPUTINFO();
        lii.cbSize = (uint)Marshal.SizeOf(lii);
        GetLastInputInfo(ref lii);
        return (uint)Environment.TickCount - lii.dwTime;
    }
}
"@

$idleMs = [IdleWslResetIdleTime]::GetIdleMilliseconds()
$idleMinutes = [math]::Round($idleMs / 60000.0, 2)
Write-Log "Idle time: $idleMinutes minutes"

# --- Ask the Python checker for the decision (DB + Prometheus + cooldown) ---
$decideResult = Invoke-Checker -CheckerArgs @("--decide", "--idle-minutes", "$idleMinutes")
Write-Log "Checker exit code: $($decideResult.ExitCode)"
Write-Log "Checker output: $($decideResult.Output)"

try {
    $decision = $decideResult.Output | ConvertFrom-Json
} catch {
    Write-Log "ERROR: could not parse checker JSON output - $_"
    exit 2
}

if ($decision.error) {
    Write-Log "Checker reported an error (failing closed): $($decision.error)"
    exit 2
}

foreach ($reason in $decision.reasons) {
    Write-Log "  reason: $reason"
}

if (-not $decision.should_reset) {
    Write-Log "Decision: NO RESET"
    exit 0
}

Write-Log "Decision: RESET (all conditions satisfied)"

if ($DryRun) {
    Write-Log "-DryRun: would reset now, but taking no action."
    exit 0
}

# --- The actual reset ---------------------------------------------------------
$freeVram = $decision.checks.free_vram_gb
Write-Log "Notifying operator before reset..."
Invoke-Checker -CheckerArgs @(
    "--notify",
    "idle-wsl-gpu-reset: bouncing WSL/Docker (idle $idleMinutes min, free VRAM $freeVram GB) to reclaim stale GPU memory."
) | Out-Null

Write-Log "Running: wsl --shutdown"
wsl --shutdown
Start-Sleep -Seconds 10

# Restart Docker Desktop. Hardcoded default install path (Docker Desktop has
# no reliable "find the running exe" API once wsl --shutdown has torn down
# its WSL-side backend) - update this path if Docker Desktop is installed
# elsewhere on this machine.
$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Log "Starting Docker Desktop: $dockerExe"
Start-Process -FilePath $dockerExe

# --- Poll the worker back to health -------------------------------------------
$maxWaitSeconds = 300
$pollIntervalSeconds = 10
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWaitSeconds) {
    Start-Sleep -Seconds $pollIntervalSeconds
    $elapsed += $pollIntervalSeconds
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8002/health" -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # Not up yet - keep polling until maxWaitSeconds.
    }
}

if ($healthy) {
    Write-Log "Stack healthy again after $elapsed seconds."
} else {
    Write-Log "WARNING: stack did not report healthy within $maxWaitSeconds seconds."
}

# --- Stamp cooldown + notify completion ----------------------------------------
Invoke-Checker -CheckerArgs @("--stamp-cooldown") | Out-Null

$completionMsg = if ($healthy) {
    "idle-wsl-gpu-reset: reset complete, stack healthy after $elapsed s."
} else {
    "idle-wsl-gpu-reset: reset complete, but stack did NOT report healthy within $maxWaitSeconds s - check the stack manually."
}
Invoke-Checker -CheckerArgs @("--notify", $completionMsg) | Out-Null

Write-Log "Done."
