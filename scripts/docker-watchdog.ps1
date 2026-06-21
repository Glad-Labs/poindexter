<#
.SYNOPSIS
    Defense-in-depth watchdog for Docker Desktop + the gladlabs compose stack.

.DESCRIPTION
    Checks:
      1. `docker info` returns 0 (engine reachable)
      2. Compose stack has the expected containers running

    Recovery:
      - Launches Docker Desktop if process not running
      - Waits for engine readiness (up to 5min)
      - Runs `docker compose up -d` to restart any stopped containers

    Why this exists: After the 2026-05-07 unexpected shutdown, Docker
    Desktop's `AutoStart` setting was False so nothing came back online -
    Grafana, Postgres, the whole stack was offline for 7+ hours. AutoStart
    is now True, but this watchdog catches the case where it silently flips
    back during a Docker Desktop update or where the engine crashes.

    Modes:
      - One-shot:  .\docker-watchdog.ps1
      - Loop:      .\docker-watchdog.ps1 -Loop -IntervalSeconds 300
      - Install:   .\docker-watchdog.ps1 -Install   (5-min scheduled task)
      - Uninstall: .\docker-watchdog.ps1 -Uninstall
#>

param(
    [switch]$Loop,
    [int]$IntervalSeconds = 300,
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = "Continue"

$DOCKER_EXE   = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# Launch from the dedicated deploy clone (auto-synced to origin/main by the
# Poindexter-DeployCheckoutSync task), NOT the main dev checkout. The main
# checkout's compose spec is not auto-advanced, so recovering the stack from it
# re-homes services to a stale spec (the 2026-06 GPU /root regression). Keep in
# sync with deploy-checkout-sync.ps1's $DeployDir.
$COMPOSE_DIR  = if ($env:POINDEXTER_DEPLOY_ROOT) { $env:POINDEXTER_DEPLOY_ROOT } else { "$env:USERPROFILE\.poindexter\deploy\glad-labs-stack" }
$COMPOSE_FILE = "docker-compose.local.yml"
$LOG_DIR      = "$env:USERPROFILE\.poindexter\logs"
$LOG_FILE     = "$LOG_DIR\docker-watchdog.log"
$TASK_NAME    = "Docker Engine Watchdog"

if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "$ts [$Level] $Message"
}

# ---- Install / Uninstall ----

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 365)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask -TaskName $TASK_NAME `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Force | Out-Null

    Write-Log "OK" "Scheduled Task '$TASK_NAME' installed (runs every 5 minutes)"
    Write-Host "Installed scheduled task: $TASK_NAME"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "OK" "Scheduled Task '$TASK_NAME' removed"
    exit 0
}

# ---- Health Checks ----

function Test-DockerEngine {
    $null = docker info 2>&1
    return ($LASTEXITCODE -eq 0)
}

function Test-DockerDesktopProcess {
    $procs = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
    return ($null -ne $procs -and @($procs).Count -gt 0)
}

function Start-DockerDesktop {
    if (-not (Test-Path $DOCKER_EXE)) {
        Write-Log "ERROR" "Docker Desktop binary missing at $DOCKER_EXE"
        return $false
    }
    Write-Log "INFO" "Launching Docker Desktop..."
    Start-Process -FilePath $DOCKER_EXE
    return $true
}

function Wait-DockerEngine {
    param([int]$MaxSeconds = 300)
    $waited = 0
    while ($waited -lt $MaxSeconds) {
        if (Test-DockerEngine) {
            Write-Log "OK" "Docker engine ready after ${waited}s"
            return $true
        }
        Start-Sleep -Seconds 10
        $waited += 10
    }
    Write-Log "ERROR" "Docker engine NOT ready after ${MaxSeconds}s"
    return $false
}

# Resolve Git Bash explicitly. On Windows the PATH ``bash`` is usually WSL's
# C:\Windows\System32\bash.exe, which runs in a separate filesystem namespace and
# cannot see Docker or the C:\ paths the stack uses. git IS on PATH, so derive Git
# Bash from git's location (<GitRoot>\cmd\git.exe -> <GitRoot>\bin\bash.exe), with
# fallbacks to the standard install locations.
function Resolve-GitBash {
    $git = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($git) {
        $cand = Join-Path (Split-Path (Split-Path $git)) 'bin\bash.exe'
        if (Test-Path $cand) { return $cand }
    }
    foreach ($p in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return 'bash'  # last resort; a failed launch surfaces if this is WSL bash
}

function Invoke-ComposeUp {
    if (-not (Test-Path "$COMPOSE_DIR\$COMPOSE_FILE")) {
        Write-Log "ERROR" "Compose file missing: $COMPOSE_DIR\$COMPOSE_FILE"
        return $false
    }
    # Launch through the deploy clone's start-stack.sh (the canonical launcher):
    # it exports bootstrap.toml secrets as env vars (so NO host .env is needed),
    # regenerates the grafana/offsite runtime env files, pins COMPOSE_PROJECT_NAME,
    # and runs ``docker compose up -d --no-build``. Routing recovery through it
    # (instead of a plain ``docker compose up`` from a stale launch dir) is what
    # keeps a Docker-restart from re-homing the stack to an out-of-date compose
    # spec. Invoked via Git Bash (NOT the PATH ``bash``, which on Windows is WSL
    # and cannot see Docker or the C:\ bind paths). Push-Location so start-stack's
    # own compose-file probe resolves against the clone regardless of task CWD.
    $bash = Resolve-GitBash
    $startStack = "$COMPOSE_DIR/scripts/start-stack.sh"
    Push-Location $COMPOSE_DIR
    try {
        $out = & $bash $startStack up -d --no-build 2>&1
        $ec = $LASTEXITCODE
        Write-Log "INFO" "start-stack up exit=$ec"
        # Compose can exit non-zero on a single-service issue while still bringing
        # the rest up - log details but don't bail.
        if ($ec -ne 0) {
            Write-Log "WARN" "start-stack output: $($out -join ' | ')"
        }
        return ($ec -eq 0)
    } finally {
        Pop-Location
    }
}

# ---- Main Check ----

function Invoke-HealthCheck {
    Write-Log "INFO" "--- Docker watchdog check ---"

    $engineOk = Test-DockerEngine
    if ($engineOk) {
        Write-Log "OK" "Docker engine reachable"
    } else {
        Write-Log "ERROR" "Docker engine unreachable"

        if (-not (Test-DockerDesktopProcess)) {
            Write-Log "WARN" "Docker Desktop process not running - launching"
            Start-DockerDesktop | Out-Null
        } else {
            Write-Log "WARN" "Docker Desktop process running but engine dead - waiting for engine"
        }

        $engineOk = Wait-DockerEngine -MaxSeconds 300
        if ($engineOk) {
            Write-Log "INFO" "Restoring compose stack"
            Invoke-ComposeUp | Out-Null
        } else {
            Write-Log "ERROR" "Engine recovery FAILED - manual intervention needed"
        }
    }

    Write-Log "INFO" "--- Docker watchdog check complete ---"
    return $engineOk
}

# ---- Run ----

if ($Loop) {
    Write-Log "INFO" "Docker watchdog starting in loop mode (interval: ${IntervalSeconds}s)"
    while ($true) {
        Invoke-HealthCheck | Out-Null
        Start-Sleep -Seconds $IntervalSeconds
    }
} else {
    $result = Invoke-HealthCheck
    exit ([int](-not $result))
}
