<#
.SYNOPSIS
    Installs both OpenClaw and Claude Code watchdogs as Windows Scheduled Tasks.

.DESCRIPTION
    Registers two tasks that run every 2 minutes:
      - "OpenClaw Watchdog" — monitors gateway + Discord/Telegram channels
      - "Claude Code Watchdog" — monitors Claude Code process + Telegram plugin

    Run with -Uninstall to remove both.

.EXAMPLE
    .\install-watchdogs.ps1            # Install both
    .\install-watchdogs.ps1 -Uninstall # Remove both
    .\install-watchdogs.ps1 -Status    # Check if installed
#>

param(
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$watchdogs = @(
    @{
        Name   = "OpenClaw Watchdog"
        Script = Join-Path $ScriptDir "openclaw-watchdog.ps1"
    },
    @{
        Name   = "Claude Code Watchdog"
        Script = Join-Path $ScriptDir "claude-code-watchdog.ps1"
    }
)

if ($Status) {
    Write-Host "Watchdog Status" -ForegroundColor Cyan
    Write-Host "===============" -ForegroundColor Cyan
    foreach ($w in $watchdogs) {
        $task = Get-ScheduledTask -TaskName $w.Name -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $w.Name -ErrorAction SilentlyContinue
            $lastRun = if ($info) { $info.LastRunTime } else { "unknown" }
            Write-Host "  $($w.Name): INSTALLED (last run: $lastRun, state: $($task.State))" -ForegroundColor Green
        } else {
            Write-Host "  $($w.Name): NOT INSTALLED" -ForegroundColor Yellow
        }
    }
    exit 0
}

if ($Uninstall) {
    foreach ($w in $watchdogs) {
        Unregister-ScheduledTask -TaskName $w.Name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed: $($w.Name)" -ForegroundColor Yellow
    }
    Write-Host "Both watchdogs uninstalled." -ForegroundColor Green
    exit 0
}

# Install both
foreach ($w in $watchdogs) {
    if (-not (Test-Path $w.Script)) {
        Write-Error "Script not found: $($w.Script)"
        continue
    }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$($w.Script)`""

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

    Register-ScheduledTask -TaskName $w.Name `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Force | Out-Null

    Write-Host "Installed: $($w.Name) (every 2 minutes)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Both watchdogs installed!" -ForegroundColor Cyan
Write-Host "Logs:" -ForegroundColor Cyan
Write-Host "  OpenClaw:    ~/.openclaw/logs/watchdog.log"
Write-Host "  Claude Code: ~/.claude/logs/claude-watchdog.log"
Write-Host ""
Write-Host "To check status: .\install-watchdogs.ps1 -Status"
Write-Host "To uninstall:    .\install-watchdogs.ps1 -Uninstall"
