# Register the DR backup as a daily Windows Task Scheduler job.
#
# Runs run-backup.sh (via Git Bash) every night at 3:00 AM local time.
# The task runs as the current user with "run whether user is logged on
# or not" so it fires on a locked screen.
#
# Idempotent - re-running overwrites the existing task. Fails loud on
# permission errors (needs to be run from an elevated PowerShell the
# first time).

$ErrorActionPreference = "Stop"

$TaskName = "GladLabs-DR-Backup"
# Resolved from this script's own location rather than hardcoded, so the task
# points at whichever copy you ran the registration from. If you deploy the
# scripts outside the repo (the operator layout is ~/.poindexter/scripts/
# dr-backup/), register from THAT copy - otherwise the task will happily run a
# checkout that a later `git pull` can move out from under it.
$ScriptPath = Join-Path $PSScriptRoot "run-backup.sh"
$BashPath = "C:\Program Files\Git\bin\bash.exe"

if (-not (Test-Path $ScriptPath)) {
    throw "Backup script not found at $ScriptPath"
}

if (-not (Test-Path $BashPath)) {
    throw "Git Bash not found at $BashPath - adjust path or install Git for Windows"
}

# Build the task components.
$Action = New-ScheduledTaskAction `
    -Execute $BashPath `
    -Argument "-lc ""$($ScriptPath -replace '\\', '/')"""

$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$Principal = New-ScheduledTaskPrincipal `
    -UserId ([Environment]::UserName) `
    -LogonType Interactive `
    -RunLevel Limited

# Register (overwrites if it already exists).
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Nightly restic snapshot of ~/.poindexter + glad-labs repos + Postgres dump to F:\poindexter-backup." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'. Next run: 3:00 AM daily."
Write-Host "To run now: schtasks /run /tn $TaskName"
Write-Host "To inspect: Get-ScheduledTask -TaskName $TaskName | Format-List *"
