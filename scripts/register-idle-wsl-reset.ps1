<#
.SYNOPSIS
    Registers/manages the idle-wsl-gpu-reset Windows Scheduled Task.

.DESCRIPTION
    Mirrors scripts/claude-sessions.ps1's Install/List/Uninstall conventions.
    The task always runs scripts/idle-wsl-gpu-reset.ps1 on a fixed poll
    interval; whether it actually resets anything is decided every run by
    the script itself (idle_wsl_reset_enabled + the rest of the conditions
    in scripts/idle_wsl_gpu_reset_check.py) - registering the task is NOT
    the same as arming it.

    Safety posture (2026-07-12, PR 2): -Install registers the task in a
    DISABLED state. It stays disabled until a -DryRun run has been reviewed
    and idle_wsl_reset_enabled is deliberately flipped true in app_settings
    AND -Enable has been run - both are separate, explicit operator actions.
    See docs/operations/idle-wsl-gpu-reset.md for the full activation
    runbook.

.PARAMETER Install
    Register the Scheduled Task (created disabled).

.PARAMETER Uninstall
    Remove the Scheduled Task.

.PARAMETER List
    Show the task's current registration + enabled/disabled state.

.PARAMETER Enable
    Enable the already-registered task so it actually starts firing on its
    poll interval. Run this ONLY after a supervised live reset has
    confirmed the stack bounces cleanly (docs/operations/idle-wsl-gpu-reset.md).

.PARAMETER Disable
    Disable the task without removing it (the reverse of -Enable).

.EXAMPLE
    .\register-idle-wsl-reset.ps1 -Install
    .\register-idle-wsl-reset.ps1 -List
    .\register-idle-wsl-reset.ps1 -Enable
    .\register-idle-wsl-reset.ps1 -Uninstall
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$List,
    [switch]$Enable,
    [switch]$Disable
)

$ErrorActionPreference = "Stop"

$TaskName = "Poindexter - idle-wsl-gpu-reset"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ResetScript = Join-Path $RepoRoot "scripts\idle-wsl-gpu-reset.ps1"
$PollIntervalMinutes = 15

function Install-IdleWslReset {
    if (-not (Test-Path $ResetScript)) {
        Write-Host "FAILED: $ResetScript not found."
        exit 1
    }

    # Windows PowerShell 5.1 directly - stable path across updates (mirrors
    # claude-sessions.ps1's Install-Sessions).
    $pwshExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

    $action = New-ScheduledTaskAction `
        -Execute $pwshExe `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ResetScript`""

    # 10 years, not [TimeSpan]::MaxValue: MaxValue serializes to
    # P99999999DT23H59M59S, which the Task Scheduler XML schema rejects as out
    # of range ("incorrectly formatted or out of range") on current Windows
    # builds under BOTH PowerShell 7 and 5.1. That silent -Install failure is
    # why the task was never registered and the whole idle-reset mechanism sat
    # dormant despite idle_wsl_reset_enabled=true (poindexter#882). A bounded
    # large value is effectively indefinite for a 15-minute poll and registers
    # cleanly.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $PollIntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
        -MultipleInstances IgnoreNew

    $null = Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

    # Registered DISABLED on purpose - see the safety posture in the header
    # comment. -Install alone must never make this task start firing.
    Disable-ScheduledTask -TaskName $TaskName | Out-Null

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Write-Host "Registered (DISABLED): $TaskName (polls every $PollIntervalMinutes min once enabled)"
        Write-Host ""
        Write-Host "This task will NOT reset anything yet. Before enabling:"
        Write-Host "  1. Run: powershell -File `"$ResetScript`" -DryRun"
        Write-Host "     Review the logged decision against the live state."
        Write-Host "  2. Set app_settings.idle_wsl_reset_enabled = true (poindexter CLI or console)."
        Write-Host "  3. Run one supervised live reset with Matt at the keyboard."
        Write-Host "  4. Only then: .\register-idle-wsl-reset.ps1 -Enable"
    } else {
        Write-Host "FAILED to register: $TaskName"
        exit 1
    }
}

function Uninstall-IdleWslReset {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "Removed: $TaskName"
}

function List-IdleWslReset {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Not registered: $TaskName"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task: $TaskName"
    Write-Host "  State: $($task.State)"
    Write-Host "  Last run: $($info.LastRunTime)  (result: $($info.LastTaskResult))"
    Write-Host "  Next run: $($info.NextRunTime)"
    Write-Host "  Poll interval: $PollIntervalMinutes minutes"
    Write-Host ""
    Write-Host "Note: registration/enable state is separate from"
    Write-Host "app_settings.idle_wsl_reset_enabled - both must be true for a"
    Write-Host "reset to actually fire. Check the log for the live decision:"
    Write-Host "  $env:USERPROFILE\.poindexter\logs\idle-wsl-gpu-reset.log"
}

function Enable-IdleWslReset {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Write-Host "FAILED: $TaskName is not registered. Run -Install first."
        exit 1
    }
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "Enabled: $TaskName - it will now poll every $PollIntervalMinutes minutes."
    Write-Host "Remember: it still only resets when app_settings.idle_wsl_reset_enabled=true."
}

function Disable-IdleWslReset {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Write-Host "Not registered: $TaskName"
        return
    }
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "Disabled: $TaskName"
}

if ($Install) { Install-IdleWslReset }
elseif ($Uninstall) { Uninstall-IdleWslReset }
elseif ($List) { List-IdleWslReset }
elseif ($Enable) { Enable-IdleWslReset }
elseif ($Disable) { Disable-IdleWslReset }
else {
    Write-Host "Usage: .\register-idle-wsl-reset.ps1 [-Install | -List | -Enable | -Disable | -Uninstall]"
}
