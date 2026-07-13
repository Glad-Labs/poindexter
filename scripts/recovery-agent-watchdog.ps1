<#
.SYNOPSIS
    Independent liveness watchdog for the Poindexter Recovery Agent (host :9841).

.DESCRIPTION
    The "Poindexter Recovery Agent" Scheduled Task is triggered "at logon,
    one-shot, non-repeating" - nothing relaunches scripts/recovery-agent.py if
    it crashes or wedges (process alive, port held, but not answering HTTP)
    until the next logon/reboot. It's the one piece of the self-healing chain
    (see docs/operations/self-healing.md) that had no self-healing of its own.

    This script is deliberately a SEPARATE process from what it watches -
    mirrors docker-watchdog.ps1's pattern, because a service can't reliably
    report its own liveness for the purpose of restarting itself.

    Checks:
      Real HTTP GET http://localhost:9841/healthz (not just "is the port open"
      or "is the process alive" - a wedged process can hold the port and stay
      in the process list while never completing a response).

    Recovery:
      Re-checks once after -WedgeConfirmSeconds before recycling, so a
      transient blip (a client-side-aborted connection, a momentary loopback
      hiccup) doesn't trigger an unnecessary restart. If still unhealthy:
      Stop-ScheduledTask + Start-ScheduledTask on "Poindexter Recovery Agent".
      Stop-ScheduledTask works even against an unresponsive process because
      Task Scheduler terminates its own child at the OS level - it doesn't
      need the target to cooperate. This DOES need the watchdog itself to run
      elevated: the target task runs elevated, and a non-elevated caller gets
      "Access is denied" (confirmed 2026-07-13 against a genuinely wedged
      instance) even as the same Windows user.

    Modes:
      - One-shot:  .\recovery-agent-watchdog.ps1
      - Loop:      .\recovery-agent-watchdog.ps1 -Loop -IntervalSeconds 300
      - Install:   .\recovery-agent-watchdog.ps1 -Install   (5-min scheduled task)
      - Uninstall: .\recovery-agent-watchdog.ps1 -Uninstall

    -WedgeConfirmSeconds (default 30): delay before re-checking a failed
    health check, so a transient blip does not trigger an unnecessary
    Stop/Start-ScheduledTask cycle.
#>

param(
    [switch]$Loop,
    [int]$IntervalSeconds = 300,
    [int]$WedgeConfirmSeconds = 30,
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = "Continue"

$AGENT_URL      = "http://localhost:9841/healthz"
$AGENT_TASK     = "Poindexter Recovery Agent"
$HEALTH_TIMEOUT = 10
$LOG_DIR        = "$env:USERPROFILE\.poindexter\logs"
$LOG_FILE       = "$LOG_DIR\recovery-agent-watchdog.log"
$TASK_NAME      = "Poindexter Recovery Agent Watchdog"

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
    # -RunLevel Highest: Stop-ScheduledTask against the (elevated) Recovery
    # Agent task needs an elevated caller, or it fails with "Access is denied"
    # even as the same Windows user - see the docstring above.
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

# ---- Health Check ----

function Test-RecoveryAgentHealthy {
    # A real HTTP round-trip, not just a port-open check - the failure mode
    # this exists for is "port held, TCP accepts, but no HTTP response ever
    # completes" (docs/operations/self-healing.md's wan-server wedge class).
    try {
        $resp = Invoke-WebRequest -Uri $AGENT_URL -TimeoutSec $HEALTH_TIMEOUT -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -eq 200 -and $resp.Content -like '*poindexter-recovery-agent*')
    } catch {
        return $false
    }
}

function Restart-RecoveryAgentTask {
    Write-Log "WARN" "Recycling '$AGENT_TASK' task"
    try {
        Stop-ScheduledTask -TaskName $AGENT_TASK -ErrorAction Stop
    } catch {
        # Already stopped (outright crash, not a wedge) is expected here, not
        # an error - Start-ScheduledTask below covers that case too.
        Write-Log "INFO" "Stop-ScheduledTask: $_"
    }
    Start-Sleep -Seconds 2
    try {
        Start-ScheduledTask -TaskName $AGENT_TASK -ErrorAction Stop
        Write-Log "OK" "Start-ScheduledTask '$AGENT_TASK' issued"
        return $true
    } catch {
        Write-Log "ERROR" "Start-ScheduledTask failed: $_"
        return $false
    }
}

function Send-TelegramAlert {
    # Mirrors docker-watchdog.ps1's helper - host-side ping for when the
    # in-Docker alert plane can't be reached either. Silent no-op if unset.
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
        $body = @{ chat_id = $chat; text = "[recovery-agent-watchdog @ $env:COMPUTERNAME] $Text" }
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" -Body $body -TimeoutSec 15 | Out-Null
        Write-Log "OK" "Telegram alert sent"
    } catch {
        Write-Log "WARN" "Telegram alert failed: $_"
    }
}

# ---- Main Check ----

function Invoke-HealthCheck {
    Write-Log "INFO" "--- Recovery Agent watchdog check ---"

    if (Test-RecoveryAgentHealthy) {
        Write-Log "OK" "Recovery Agent healthy"
        Write-Log "INFO" "--- Recovery Agent watchdog check complete ---"
        return $true
    }

    Write-Log "WARN" "Recovery Agent healthz failed - confirming (recheck in ${WedgeConfirmSeconds}s)"
    Start-Sleep -Seconds $WedgeConfirmSeconds
    if (Test-RecoveryAgentHealthy) {
        Write-Log "OK" "Recovered on recheck - transient blip, no recycle needed"
        Write-Log "INFO" "--- Recovery Agent watchdog check complete ---"
        return $true
    }

    Write-Log "ERROR" "Still unhealthy after recheck - recycling"
    Send-TelegramAlert "Recovery Agent unresponsive at $AGENT_URL - recycling the scheduled task."
    $recovered = Restart-RecoveryAgentTask
    if ($recovered) {
        Start-Sleep -Seconds 5
        if (Test-RecoveryAgentHealthy) {
            Write-Log "OK" "Recovery Agent healthy after recycle"
        } else {
            Write-Log "ERROR" "Recovery Agent still unhealthy immediately after recycle - may need more startup time or manual intervention"
            Send-TelegramAlert "Recovery Agent still unresponsive after recycle - check manually."
        }
    } else {
        Send-TelegramAlert "Recovery Agent recycle FAILED (Start-ScheduledTask error) - manual intervention needed."
    }

    Write-Log "INFO" "--- Recovery Agent watchdog check complete ---"
    return $recovered
}

# ---- Run ----

if ($Loop) {
    Write-Log "INFO" "Recovery Agent watchdog starting in loop mode (interval: ${IntervalSeconds}s)"
    while ($true) {
        Invoke-HealthCheck | Out-Null
        Start-Sleep -Seconds $IntervalSeconds
    }
} else {
    $result = Invoke-HealthCheck
    exit ([int](-not $result))
}
