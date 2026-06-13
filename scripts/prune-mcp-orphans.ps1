<#
.SYNOPSIS
    Periodic sweep that reaps orphaned Claude Code MCP-server processes.

.DESCRIPTION
    A Claude Code session that is force-killed / crashes / OOMs leaves its MCP
    server child tree behind, because Windows does not reap child trees the way
    Unix does. This registers a Windows Scheduled Task that runs
    scripts/prune_mcp_orphans.py windowless (pythonw.exe) every 30 minutes to
    clean up genuine orphans only. See prune_mcp_orphans.py for the (provable)
    safety rules — the Docker stack, your worker/brain, and live Claude sessions
    are never touched.

    The task runs as the current user and only ever kills the current user's
    processes, so NEITHER registration NOR execution requires elevation.

.PARAMETER Install
    Register the task in DRY-RUN mode (logs would-be kills, kills nothing).
    This is the recommended starting posture: watch the log for a few days,
    confirm it only ever flags genuine orphans, then -Enforce.

.PARAMETER Enforce
    Re-register the task in ENFORCE mode (actually kills orphans).

.PARAMETER Uninstall
    Remove the task.

.PARAMETER List
    Show task status, current mode (dry-run vs enforce), and the last log lines.

.PARAMETER RunNow
    Run one sweep right now in dry-run (visible) and print the result.

.NOTES
    Log: %USERPROFILE%\.poindexter\mcp-orphan-sweep.log

.EXAMPLE
    .\prune-mcp-orphans.ps1 -Install     # dry-run, every 30 min
    .\prune-mcp-orphans.ps1 -RunNow      # one dry-run sweep, see what it flags
    .\prune-mcp-orphans.ps1 -List        # status + recent log
    .\prune-mcp-orphans.ps1 -Enforce     # flip to actually killing orphans
    .\prune-mcp-orphans.ps1 -Uninstall
#>
param(
    [switch]$Install,
    [switch]$Enforce,
    [switch]$Uninstall,
    [switch]$List,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

# Resolve the repo root from this script's own location (scripts/ -> repo root),
# so there's no hard-coded operator home path and it works wherever the repo lives.
$WorkDir         = Split-Path -Parent $PSScriptRoot
$PythonW         = "$WorkDir\.venv\Scripts\pythonw.exe"
$Python          = "$WorkDir\.venv\Scripts\python.exe"
$Script          = "$WorkDir\scripts\prune_mcp_orphans.py"
$TaskName        = "poindexter-mcp-orphan-sweep"
$IntervalMinutes = 30
$LogPath         = Join-Path $env:USERPROFILE ".poindexter\mcp-orphan-sweep.log"

function Register-Sweep([bool]$DryRun) {
    $argument = "`"$Script`""
    if ($DryRun) { $argument += " --dry-run" }

    $action = New-ScheduledTaskAction -Execute $PythonW -Argument $argument -WorkingDirectory $WorkDir

    # A daily anchor whose repetition fires every $IntervalMinutes for 24h =
    # a continuous N-minute cadence. This two-trigger composition is the robust
    # idiom (a lone -Once trigger's RepetitionDuration default varies by build).
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).Date.AddHours(3)
    $trigger.Repetition = (
        New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
            -RepetitionDuration (New-TimeSpan -Days 1)
    ).Repetition

    # Short time limit (a sweep takes <1s), skip nothing on battery, catch up if
    # the machine was asleep at a fire time, and never stack instances.
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    $mode = if ($DryRun) { "DRY-RUN" } else { "ENFORCE" }
    $desc = "Reap orphaned Claude Code MCP-server processes every $IntervalMinutes min. Mode: $mode. See scripts/prune_mcp_orphans.py."

    $null = Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $desc `
        -Force | Out-Null

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        $label = if ($DryRun) { "DRY-RUN (logs only, kills nothing)" } else { "ENFORCE (kills orphans)" }
        Write-Host "Registered: $TaskName - every $IntervalMinutes min - $label"
        Write-Host "Log: $LogPath"
        if ($DryRun) {
            Write-Host "When the log looks right, flip it live with: .\prune-mcp-orphans.ps1 -Enforce"
        }
    } else {
        Write-Host "FAILED to register: $TaskName"
    }
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "$TaskName - NOT REGISTERED"
        Write-Host "Install with: .\prune-mcp-orphans.ps1 -Install"
        return
    }
    $argument = $task.Actions[0].Arguments
    $mode = if ($argument -match "--dry-run") { "DRY-RUN (logs only)" } else { "ENFORCE (kills orphans)" }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "$TaskName - $($task.State) - $mode"
    Write-Host "  Last run : $($info.LastRunTime)  (result $($info.LastTaskResult))"
    Write-Host "  Next run : $($info.NextRunTime)"
    if (Test-Path $LogPath) {
        Write-Host "  --- last 12 log lines ($LogPath) ---"
        Get-Content $LogPath -Tail 12 | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  (no log yet at $LogPath)"
    }
}

if ($Install) {
    Register-Sweep $true        # dry-run first — the recommended starting posture
} elseif ($Enforce) {
    Register-Sweep $false
} elseif ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: $TaskName"
} elseif ($List) {
    Show-Status
} elseif ($RunNow) {
    & $Python "$Script" --dry-run --verbose
} else {
    Write-Host "Usage: .\prune-mcp-orphans.ps1 -Install | -Enforce | -Uninstall | -List | -RunNow"
    Write-Host ""
    Show-Status
}
