<#
.SYNOPSIS
    Docker cleanup script - reclaims disk space from unused images and build cache.

.DESCRIPTION
    Safely removes unused Docker resources without touching volumes (data).
    Only prunes images and build cache older than 7 days.

.PARAMETER DryRun
    Show what would be removed without actually removing anything.

.PARAMETER Install
    Register as a weekly Windows Scheduled Task (runs Sundays at 3 AM).

.PARAMETER Uninstall
    Remove the scheduled task.

.EXAMPLE
    .\docker-prune.ps1              # Run cleanup now
    .\docker-prune.ps1 -DryRun     # Preview what would be cleaned
    .\docker-prune.ps1 -Install    # Register weekly task
#>
param(
    [switch]$DryRun,
    [switch]$Install,
    [switch]$Uninstall
)

$TaskName = "Poindexter Docker Prune"

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "Weekly Docker cleanup - prunes unused images and build cache" -Force
    Write-Host "Installed scheduled task: $TaskName (Sundays at 3 AM)"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

Write-Host "=== Docker Disk Usage (Before) ==="
docker system df

if ($DryRun) {
    Write-Host ""
    Write-Host "=== DRY RUN - Would remove: ==="
    Write-Host ""
    Write-Host "Dangling images:"
    docker image ls --filter "dangling=true" --format "  {{.Repository}}:{{.Tag}} ({{.Size}})"
    Write-Host ""
    Write-Host "Unused images older than 7 days:"
    docker image ls --filter "dangling=false" --format "  {{.Repository}}:{{.Tag}} {{.CreatedSince}} ({{.Size}})" | Select-String "weeks|months|years"
    Write-Host ""
    Write-Host "Build cache:"
    docker builder du 2>$null | Select-Object -Last 1
    Write-Host ""
    Write-Host "NOTE: Volumes are NEVER pruned (data safety)."
    exit 0
}

Write-Host ""
Write-Host "=== Pruning dangling images ==="
docker image prune -f

Write-Host ""
Write-Host "=== Pruning unused images older than 7 days ==="
docker image prune -a -f --filter "until=168h"

Write-Host ""
Write-Host "=== Pruning build cache older than 7 days ==="
docker builder prune -f --filter "until=168h"

Write-Host ""
Write-Host "=== Docker Disk Usage (After) ==="
docker system df

Write-Host ""
Write-Host "Done. Volumes were NOT touched."
