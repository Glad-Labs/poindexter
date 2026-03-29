<#
.SYNOPSIS
    Install/uninstall the local worker as a Windows Scheduled Task that starts on login.

.EXAMPLE
    .\scripts\install-worker.ps1            # Install (starts on login)
    .\scripts\install-worker.ps1 -Uninstall # Remove
    .\scripts\install-worker.ps1 -Status    # Check status
#>

param(
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$TASK_NAME = "Glad Labs Worker"
$ScriptPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "start-worker.ps1"

if ($Status) {
    $task = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TASK_NAME -ErrorAction SilentlyContinue
        Write-Host "$TASK_NAME`: INSTALLED (state: $($task.State), last run: $($info.LastRunTime))" -ForegroundColor Green
    } else {
        Write-Host "$TASK_NAME`: NOT INSTALLED" -ForegroundColor Yellow
    }
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: $TASK_NAME" -ForegroundColor Yellow
    exit 0
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TASK_NAME `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Force | Out-Null

Write-Host "Installed: $TASK_NAME (starts on login)" -ForegroundColor Green
Write-Host "Worker will connect to production Railway DB and process content tasks using local Ollama."
Write-Host ""
Write-Host "To check: .\scripts\install-worker.ps1 -Status"
Write-Host "To remove: .\scripts\install-worker.ps1 -Uninstall"
