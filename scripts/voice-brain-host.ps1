<#
.SYNOPSIS
    Install / control the always-on voice host-brain daemon as a hidden,
    self-restarting Windows scheduled task (Glad-Labs/poindexter#1006).

.DESCRIPTION
    The claude-code voice room runs its `claude -p` dev brain on the HOST (full
    repo + git + write + every host MCP) via scripts/voice_brain_host.py, while
    the container only shuttles audio. That daemon must survive logoff/reboot,
    so this registers it as a scheduled task that:

      * starts at logon (the daemon needs the operator's interactive session so
        `claude` runs under the user's OAuth, not SYSTEM),
      * runs HIDDEN under pythonw (no console window - hidden-background-job
        policy); the daemon auto-logs to ~/.poindexter/voice_brain_host.log
        because pythonw has no stderr,
      * auto-restarts on crash (RestartCount/RestartInterval),
      * has no execution time limit (it is a long-lived daemon).

    The daemon is self-configuring: the bearer token is read from
    ~/.poindexter/voice_brain_token and the repo root is derived from the
    script's own location, so the task definition carries NO secret and NO
    operator-specific path (this script ships in the public mirror).

.PARAMETER Install
    Register (or re-register) the scheduled task, then start it.

.PARAMETER Uninstall
    Stop and remove the scheduled task.

.PARAMETER Start
    Start the task now (e.g. after Install on an already-logged-on session).

.PARAMETER Stop
    Stop the running daemon.

.PARAMETER Status
    Show the task state + whether the daemon answers on /healthz.

.EXAMPLE
    .\scripts\voice-brain-host.ps1 -Install
    .\scripts\voice-brain-host.ps1 -Status
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$TaskName = 'Glad Labs - Voice Host Brain'
$RepoRoot = Split-Path -Parent $PSScriptRoot          # scripts/ -> repo root
$Daemon   = Join-Path $PSScriptRoot 'voice_brain_host.py'
$Port     = 8123

function Resolve-Pythonw {
    # The daemon is pure-stdlib, so any Python 3 works. Prefer pythonw.exe
    # (windowless). Fall back to deriving it from python.exe on PATH.
    $pw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
    if ($pw) { return $pw.Source }
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue)
    if ($py) {
        $candidate = Join-Path (Split-Path -Parent $py.Source) 'pythonw.exe'
        if (Test-Path $candidate) { return $candidate }
        return $py.Source   # last resort: python.exe (will flash a console)
    }
    throw 'Neither pythonw.exe nor python.exe found on PATH.'
}

function Install-Task {
    if (-not (Test-Path $Daemon)) { throw "Daemon not found: $Daemon" }
    $pythonw = Resolve-Pythonw
    Write-Host "Python:  $pythonw"
    Write-Host "Daemon:  $Daemon"
    Write-Host "Workdir: $RepoRoot"

    # Remove any prior registration so -Install is idempotent.
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$Daemon`"" -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # Interactive principal (not SYSTEM): claude needs the user's OAuth context.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Always-on voice host-brain daemon (claude-code room, #1006). Hidden, self-restarting.' | Out-Null

    Write-Host "Registered: $TaskName (AtLogOn, hidden, restart-on-crash)"
    Start-Task
}

function Start-Task {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started: $TaskName"
    Start-Sleep -Seconds 2
    Show-Status
}

function Stop-Task {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "Stopped: $TaskName"
}

function Uninstall-Task {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: $TaskName"
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Task NOT registered ($TaskName). Run -Install."
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task:        $TaskName"
    Write-Host "State:       $($task.State)"
    Write-Host "LastRun:     $($info.LastRunTime)  (result 0x$('{0:X}' -f $info.LastTaskResult))"
    # Probe the daemon's unauthenticated health endpoint.
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port/healthz" -TimeoutSec 3 -UseBasicParsing
        Write-Host "Daemon:      UP (/healthz -> HTTP $($resp.StatusCode))"
    } catch {
        Write-Host "Daemon:      not answering on :$Port (/healthz). Check ~/.poindexter/voice_brain_host.log"
    }
}

if     ($Install)   { Install-Task }
elseif ($Uninstall) { Uninstall-Task }
elseif ($Start)     { Start-Task }
elseif ($Stop)      { Stop-Task }
elseif ($Status)    { Show-Status }
else {
    Write-Host 'Usage:'
    Write-Host '  .\scripts\voice-brain-host.ps1 -Install     Register hidden self-restarting task + start'
    Write-Host '  .\scripts\voice-brain-host.ps1 -Start       Start the daemon now'
    Write-Host '  .\scripts\voice-brain-host.ps1 -Stop        Stop the daemon'
    Write-Host '  .\scripts\voice-brain-host.ps1 -Status      Task state + /healthz probe'
    Write-Host '  .\scripts\voice-brain-host.ps1 -Uninstall   Remove the task'
}
