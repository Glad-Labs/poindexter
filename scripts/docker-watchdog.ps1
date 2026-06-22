<#
.SYNOPSIS
    Defense-in-depth watchdog for Docker Desktop + the gladlabs compose stack.

.DESCRIPTION
    Checks:
      1. `docker info` returns 0 (engine reachable)
      2. Compose stack has the expected containers running

    Recovery:
      - Launches Docker Desktop if the process is not running (AutoStart-off case)
      - If the process IS running but the engine is unreachable - the wedged
        WSL2-backend signature (engine 500 / HCS_E_CONNECTION_TIMEOUT) - confirms
        it is not a transient blip, captures forensics + pings Telegram, then
        force-recycles the VM with `wsl --shutdown` (Docker Desktop AutoStart
        rebuilds it). A plain relaunch cannot fix this case.
      - Waits for engine readiness (up to 5min)
      - Runs the deploy clone's start-stack.sh to restart any stopped containers

    Why this exists: After the 2026-05-07 unexpected shutdown, Docker
    Desktop's `AutoStart` setting was False so nothing came back online -
    Grafana, Postgres, the whole stack was offline for 7+ hours. AutoStart
    is now True, but this watchdog catches the case where it silently flips
    back during a Docker Desktop update or where the engine crashes. The
    2026-06-21 outage added the wedged-VM path: the engine returned 500 with
    the Docker Desktop process still alive, so the old "wait then give up"
    recovery could not help - only `wsl --shutdown` clears it.

    Modes:
      - One-shot:  .\docker-watchdog.ps1
      - Loop:      .\docker-watchdog.ps1 -Loop -IntervalSeconds 300
      - Install:   .\docker-watchdog.ps1 -Install   (5-min scheduled task)
      - Uninstall: .\docker-watchdog.ps1 -Uninstall

    -WedgeConfirmSeconds (default 30): delay before re-checking a dead engine
    when Docker Desktop is alive, so a transient `docker info` blip does not
    trigger an unnecessary VM recycle.
#>

param(
    [switch]$Loop,
    [int]$IntervalSeconds = 300,
    [int]$WedgeConfirmSeconds = 30,
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

# ---- WSL2-backend wedge recovery ----
# The case the original watchdog could NOT recover: Docker Desktop's process is
# alive, but the WSL2 utility VM is unresponsive - `docker info` returns 500 and
# `wsl` calls time out with HCS_E_CONNECTION_TIMEOUT. Relaunching Docker Desktop
# does nothing; the VM must be force-recycled. Added after the 2026-06-21 outage
# where the engine stayed wedged and recovery only logged "manual intervention
# needed" (the manual step was a human running `wsl --shutdown`).

function Reset-WslBackend {
    # Force-terminate the wedged WSL2 utility VM. Returns even when the VM is
    # unresponsive because this is a host-side HCS teardown, not an in-VM command.
    # Docker Desktop (AutoStart on) rebuilds the VM + engine afterward.
    Write-Log "WARN" "Recycling WSL2 backend: wsl --shutdown"
    & wsl.exe --shutdown 2>$null
    Write-Log "INFO" "wsl --shutdown issued (exit=$LASTEXITCODE)"
    # Let the VM fully tear down before Docker Desktop starts rebuilding it.
    Start-Sleep -Seconds 10
}

function Save-WedgeForensics {
    # Best-effort host-state capture at the moment of a wedge so the (still
    # unconfirmed) root cause can finally be pinned. Never throws - recovery must
    # proceed even if capture fails.
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $dir = Join-Path $LOG_DIR "wedge-$stamp"
    try {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Log "INFO" "Capturing wedge forensics -> $dir"

        # GPU snapshot (host nvidia-smi). WSL2 GPU-passthrough churn is the leading
        # hypothesis for the VM hang, so this is the highest-value grab.
        try { & nvidia-smi *> (Join-Path $dir "nvidia-smi.txt") } catch {}

        # Hyper-V / WSL / vmcompute event channels around the wedge.
        $since = (Get-Date).AddMinutes(-20)
        foreach ($log in @('System', 'Microsoft-Windows-Hyper-V-Compute-Operational')) {
            try {
                Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $since } -ErrorAction Stop |
                    Where-Object { $_.LevelDisplayName -in 'Error', 'Critical', 'Warning' } |
                    Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message |
                    Format-List |
                    Out-File -FilePath (Join-Path $dir ("events-" + ($log -replace '[\\/:]+', '_') + ".txt")) -Encoding utf8
            } catch {}
        }

        # Docker Desktop's own diagnostic bundle (path varies by version; best-effort).
        $diag = Join-Path $env:ProgramFiles "Docker\Docker\resources\com.docker.diagnose.exe"
        if (Test-Path $diag) {
            try { & $diag gather -output (Join-Path $dir "docker-diagnose.zip") *> (Join-Path $dir "diagnose.log") } catch {}
        }

        Write-Log "OK" "Wedge forensics captured -> $dir"
    } catch {
        Write-Log "WARN" "Forensics capture failed: $_"
    }
}

function Send-TelegramAlert {
    # Host-side Telegram ping. A fully-down engine is exactly when the in-Docker
    # alert plane - brain dispatcher, Alertmanager, the Prometheus dead-man's
    # switch - is ALSO down, so the watchdog pings directly. Token is read from
    # bootstrap.toml; silent no-op if unset. Never blocks recovery.
    param([string]$Text)
    try {
        $bootstrap = Join-Path $env:USERPROFILE ".poindexter\bootstrap.toml"
        if (-not (Test-Path $bootstrap)) { Write-Log "INFO" "Telegram skipped (no bootstrap.toml)"; return }
        $toml = Get-Content $bootstrap -Raw
        $token = ([regex]::Match($toml, '(?m)^\s*telegram_bot_token\s*=\s*"([^"]*)"')).Groups[1].Value
        $chat = ([regex]::Match($toml, '(?m)^\s*telegram_chat_id\s*=\s*"([^"]*)"')).Groups[1].Value
        if ([string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($chat)) {
            Write-Log "INFO" "Telegram skipped (token/chat_id not set in bootstrap.toml)"
            return
        }
        $body = @{ chat_id = $chat; text = "[docker-watchdog @ $env:COMPUTERNAME] $Text" }
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" -Body $body -TimeoutSec 15 | Out-Null
        Write-Log "OK" "Telegram alert sent"
    } catch {
        Write-Log "WARN" "Telegram alert failed: $_"
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
            # Docker Desktop isn't running at all - just (re)launch it. The original
            # AutoStart-flipped-off case.
            Write-Log "WARN" "Docker Desktop process not running - launching"
            Start-DockerDesktop | Out-Null
        } else {
            # Process alive but engine dead = the wedged-WSL2-VM signature. A relaunch
            # can't fix it; the VM must be force-recycled. Guard against a transient
            # `docker info` blip by re-checking after a short delay first.
            Write-Log "WARN" "Docker Desktop alive but engine dead - confirming wedge (recheck in ${WedgeConfirmSeconds}s)"
            Start-Sleep -Seconds $WedgeConfirmSeconds
            if (Test-DockerEngine) {
                Write-Log "OK" "Engine recovered on recheck - transient blip, no recycle needed"
                Write-Log "INFO" "--- Docker watchdog check complete ---"
                return $true
            }
            Write-Log "ERROR" "Engine still dead after recheck - WSL2 backend wedged, recycling"
            Save-WedgeForensics
            Send-TelegramAlert "Docker engine wedged - recycling WSL2 backend (wsl --shutdown). Stack auto-restores."
            Reset-WslBackend
        }

        $engineOk = Wait-DockerEngine -MaxSeconds 300
        if ($engineOk) {
            Write-Log "INFO" "Restoring compose stack"
            Invoke-ComposeUp | Out-Null
        } else {
            Write-Log "ERROR" "Engine recovery FAILED after recycle - manual intervention needed"
            Send-TelegramAlert "Docker engine STILL down after wsl --shutdown - manual intervention needed."
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
