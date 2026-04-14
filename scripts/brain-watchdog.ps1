<#
.SYNOPSIS
    Brain daemon watchdog — Layer 1 of Poindexter's redundancy model.

.DESCRIPTION
    Monitors the brain daemon's heartbeat file. If it goes stale (no update
    in 15 minutes), restarts the brain and sends a Telegram alert.

    This runs at the OS level (Task Scheduler) so it works even when the
    brain, Postgres, Docker, and everything else is dead.

.PARAMETER Install
    Register this script as a Windows Scheduled Task (runs every 10 minutes).

.PARAMETER Uninstall
    Remove the scheduled task.

.EXAMPLE
    .\brain-watchdog.ps1            # Run once (check heartbeat now)
    .\brain-watchdog.ps1 -Install   # Register as scheduled task
    .\brain-watchdog.ps1 -Uninstall # Remove scheduled task
#>
param(
    [switch]$Install,
    [switch]$Uninstall
)

$TaskName = "Poindexter Brain Watchdog"
$HeartbeatPath = Join-Path $env:USERPROFILE ".poindexter\heartbeat"
$BrainScript = Join-Path $PSScriptRoot "..\brain\brain_daemon.py"
$LogDir = Join-Path $env:USERPROFILE ".poindexter\logs"
$LogFile = Join-Path $LogDir "watchdog.log"
$MaxStaleMinutes = 15

# Ensure log directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -FilePath $LogFile
}

# --- Install / Uninstall ---

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) `
        -Once -At (Get-Date)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "Monitors Poindexter brain daemon heartbeat" -Force
    Write-Host "Installed scheduled task: $TaskName (every 10 minutes)"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

# --- Heartbeat Check ---

# If heartbeat file doesn't exist, brain has never run — start it
if (-not (Test-Path $HeartbeatPath)) {
    Write-Log "WARN: No heartbeat file found — brain may have never started"
    # Try to start the brain
    if (Test-Path $BrainScript) {
        Write-Log "ACTION: Starting brain daemon"
        Start-Process pythonw -ArgumentList $BrainScript -WindowStyle Hidden
        Write-Log "OK: Brain daemon started"
    } else {
        Write-Log "ERROR: Brain script not found at $BrainScript"
    }
    exit 0
}

# Read heartbeat and check freshness
try {
    $content = Get-Content $HeartbeatPath -Raw | ConvertFrom-Json
    $heartbeatTime = [DateTimeOffset]::FromUnixTimeSeconds([long]$content.ts).LocalDateTime
    $age = (Get-Date) - $heartbeatTime
    $ageMinutes = [math]::Round($age.TotalMinutes, 1)
} catch {
    # Legacy format (just a timestamp number) or corrupt file
    try {
        $ts = [double](Get-Content $HeartbeatPath -Raw)
        $heartbeatTime = [DateTimeOffset]::FromUnixTimeSeconds([long]$ts).LocalDateTime
        $age = (Get-Date) - $heartbeatTime
        $ageMinutes = [math]::Round($age.TotalMinutes, 1)
    } catch {
        Write-Log "ERROR: Cannot parse heartbeat file"
        $ageMinutes = 999
    }
}

if ($ageMinutes -lt $MaxStaleMinutes) {
    # Brain is alive — nothing to do
    exit 0
}

# --- Brain is stale — take action ---

Write-Log "ALERT: Brain heartbeat is $ageMinutes minutes old (threshold: $MaxStaleMinutes)"

# Kill any existing brain process
$brainProcs = Get-Process python*, pythonw* -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*brain_daemon*" }
foreach ($proc in $brainProcs) {
    Write-Log "ACTION: Killing stale brain process PID $($proc.Id)"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

# Restart the brain
if (Test-Path $BrainScript) {
    Start-Process pythonw -ArgumentList $BrainScript -WindowStyle Hidden
    Write-Log "ACTION: Brain daemon restarted"
} else {
    Write-Log "ERROR: Brain script not found at $BrainScript"
}

# Send Telegram alert (direct API call — no dependencies)
$telegramToken = $env:TELEGRAM_BOT_TOKEN
$telegramChatId = $env:TELEGRAM_CHAT_ID

# Try loading from OpenClaw .env if not in environment
if (-not $telegramToken) {
    $envFile = Join-Path $env:USERPROFILE ".openclaw\workspace\.env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match "^TELEGRAM_BOT_TOKEN=(.+)$") { $telegramToken = $Matches[1] }
            if ($_ -match "^TELEGRAM_CHAT_ID=(.+)$") { $telegramChatId = $Matches[1] }
        }
    }
}

if ($telegramToken -and $telegramChatId) {
    $text = "Brain Watchdog: daemon was unresponsive for $ageMinutes minutes. Restarted automatically."
    try {
        $body = @{ chat_id = $telegramChatId; text = $text } | ConvertTo-Json
        Invoke-RestMethod -Uri "https://api.telegram.org/bot$telegramToken/sendMessage" `
            -Method Post -ContentType "application/json" -Body $body | Out-Null
        Write-Log "OK: Telegram alert sent"
    } catch {
        Write-Log "ERROR: Telegram alert failed: $_"
    }
} else {
    Write-Log "WARN: No Telegram credentials — alert not sent"
}
