<#
.SYNOPSIS
    Self-healing watchdog for OpenClaw gateway + channel connections.

.DESCRIPTION
    Checks:
      1. OpenClaw gateway process is running
      2. Gateway HTTP health endpoint responds
      3. Telegram channel is connected
      4. Discord channel is connected

    If any check fails, attempts auto-recovery:
      - Restart gateway if process dead or HTTP unresponsive
      - Reconnect channels via gateway restart (channels reconnect on boot)

    Can run as:
      - One-shot: .\openclaw-watchdog.ps1
      - Loop:     .\openclaw-watchdog.ps1 -Loop -IntervalSeconds 60
      - Install as Scheduled Task: .\openclaw-watchdog.ps1 -Install

.EXAMPLE
    .\openclaw-watchdog.ps1                          # One check
    .\openclaw-watchdog.ps1 -Loop                    # Loop every 120s
    .\openclaw-watchdog.ps1 -Loop -IntervalSeconds 60
    .\openclaw-watchdog.ps1 -Install                 # Windows Scheduled Task (every 2 min)
    .\openclaw-watchdog.ps1 -Uninstall               # Remove Scheduled Task
#>

param(
    [switch]$Loop,
    [int]$IntervalSeconds = 120,
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = "Continue"

$GATEWAY_PORT = 18789
$GATEWAY_URL = "http://localhost:$GATEWAY_PORT"
$LOG_DIR = "$env:USERPROFILE\.openclaw\logs"
$LOG_FILE = "$LOG_DIR\watchdog.log"
$TASK_NAME = "OpenClaw Watchdog"
$MAX_RESTART_ATTEMPTS = 3
$RESTART_COOLDOWN_SECONDS = 30

# Ensure log directory
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts [$Level] $Message"
    Add-Content -Path $LOG_FILE -Value $line
    switch ($Level) {
        "OK"    { Write-Host $line -ForegroundColor Green }
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "ERROR" { Write-Host $line -ForegroundColor Red }
        default { Write-Host $line }
    }
}

# ---- Scheduled Task Install/Uninstall ----

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 2) `
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
        -Force

    Write-Log "OK" "Scheduled Task '$TASK_NAME' installed (runs every 2 minutes)"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "OK" "Scheduled Task '$TASK_NAME' removed"
    exit 0
}

# ---- Health Checks ----

function Test-GatewayProcess {
    $procs = Get-Process -Name "node" -ErrorAction SilentlyContinue |
        Where-Object {
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
                $cmd -and $cmd -match "openclaw"
            } catch { $false }
        }
    return ($null -ne $procs -and @($procs).Count -gt 0)
}

function Test-GatewayHTTP {
    try {
        $resp = Invoke-WebRequest -Uri "$GATEWAY_URL/status" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-ChannelStatus {
    param([string]$Channel)
    try {
        $resp = Invoke-WebRequest -Uri "$GATEWAY_URL/status" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        $status = $resp.Content | ConvertFrom-Json

        # Check channels array or object for the named channel
        $channels = $status.channels
        if ($null -eq $channels) { return $null }  # Can't determine

        if ($channels -is [array]) {
            $ch = $channels | Where-Object { $_.name -eq $Channel -or $_.id -eq $Channel }
            if ($ch) { return ($ch.status -eq "connected" -or $ch.connected -eq $true) }
        }
        elseif ($channels.$Channel) {
            $ch = $channels.$Channel
            return ($ch.status -eq "connected" -or $ch.connected -eq $true)
        }

        return $null  # Channel info not available in status response
    } catch {
        return $null
    }
}

function Start-Gateway {
    Write-Log "INFO" "Starting OpenClaw gateway..."
    try {
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-Command", "openclaw gateway start" `
            -WindowStyle Hidden
        Start-Sleep -Seconds 10
        return (Test-GatewayHTTP)
    } catch {
        Write-Log "ERROR" "Failed to start gateway: $_"
        return $false
    }
}

function Restart-Gateway {
    Write-Log "INFO" "Restarting OpenClaw gateway..."
    try {
        $result = & openclaw gateway restart 2>&1
        Start-Sleep -Seconds 10
        return (Test-GatewayHTTP)
    } catch {
        Write-Log "ERROR" "Failed to restart gateway: $_"
        # Fallback: kill and start fresh
        Write-Log "INFO" "Attempting kill + fresh start..."
        Get-Process -Name "node" -ErrorAction SilentlyContinue |
            Where-Object {
                try {
                    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
                    $cmd -and $cmd -match "openclaw"
                } catch { $false }
            } | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        return (Start-Gateway)
    }
}

# ---- Main Check ----

function Invoke-HealthCheck {
    Write-Log "INFO" "--- Watchdog check starting ---"
    $healthy = $true
    $restarted = $false

    # 1. Gateway process
    if (Test-GatewayProcess) {
        Write-Log "OK" "Gateway process running"
    } else {
        Write-Log "ERROR" "Gateway process NOT found"
        $healthy = $false
    }

    # 2. Gateway HTTP
    if (Test-GatewayHTTP) {
        Write-Log "OK" "Gateway HTTP responding"
    } else {
        Write-Log "ERROR" "Gateway HTTP not responding"
        $healthy = $false
    }

    # 3. If gateway unhealthy, restart it
    if (-not $healthy) {
        $attempts = 0
        while ($attempts -lt $MAX_RESTART_ATTEMPTS) {
            $attempts++
            Write-Log "WARN" "Recovery attempt $attempts/$MAX_RESTART_ATTEMPTS"

            if (-not (Test-GatewayProcess)) {
                $ok = Start-Gateway
            } else {
                $ok = Restart-Gateway
            }

            if ($ok) {
                Write-Log "OK" "Gateway recovered after attempt $attempts"
                $restarted = $true
                break
            }

            if ($attempts -lt $MAX_RESTART_ATTEMPTS) {
                Write-Log "INFO" "Waiting ${RESTART_COOLDOWN_SECONDS}s before next attempt..."
                Start-Sleep -Seconds $RESTART_COOLDOWN_SECONDS
            }
        }

        if (-not $ok) {
            Write-Log "ERROR" "Gateway recovery FAILED after $MAX_RESTART_ATTEMPTS attempts"
            return $false
        }
    }

    # 4. Channel checks (after gateway is confirmed up)
    $telegramStatus = Test-ChannelStatus "telegram"
    $discordStatus = Test-ChannelStatus "discord"

    if ($null -eq $telegramStatus) {
        Write-Log "INFO" "Telegram status: unknown (status endpoint doesn't expose channel details)"
    } elseif ($telegramStatus) {
        Write-Log "OK" "Telegram: connected"
    } else {
        Write-Log "WARN" "Telegram: disconnected"
        if (-not $restarted) {
            Write-Log "INFO" "Restarting gateway to reconnect channels..."
            Restart-Gateway
        }
    }

    if ($null -eq $discordStatus) {
        Write-Log "INFO" "Discord status: unknown (status endpoint doesn't expose channel details)"
    } elseif ($discordStatus) {
        Write-Log "OK" "Discord: connected"
    } else {
        Write-Log "WARN" "Discord: disconnected"
        if (-not $restarted) {
            Write-Log "INFO" "Restarting gateway to reconnect channels..."
            Restart-Gateway
        }
    }

    Write-Log "INFO" "--- Watchdog check complete ---"
    return $true
}

# ---- Run ----

if ($Loop) {
    Write-Log "INFO" "Watchdog starting in loop mode (interval: ${IntervalSeconds}s)"
    while ($true) {
        Invoke-HealthCheck
        Start-Sleep -Seconds $IntervalSeconds
    }
} else {
    $result = Invoke-HealthCheck
    exit ([int](-not $result))
}
