<#
.SYNOPSIS
  Keep the dedicated deploy checkout in sync with origin/main.

.DESCRIPTION
  Glad-Labs/poindexter#228 — companion to scripts/setup-deploy-checkout.sh.

  Once the stack's runtime code mounts are repointed at the dedicated deploy
  clone (POINDEXTER_DEPLOY_ROOT=~/.poindexter/deploy/glad-labs-stack), that
  clone is what the worker (and the other src-mounting services) actually run.
  It therefore needs to be advanced to origin/main as merges land — otherwise
  the relocated worker would run pinned clone-time code forever.

  This script does the host-side half of the genuine self-heal loop:

    git fetch origin main --prune          # network half (creds live on host)
    git reset --hard origin/main           # advance the working tree
    git clean -fd                          # wipe any stray untracked file

  The in-brain-container migration-drift probe does the SAME reset (auth-free,
  on the already-fetched origin/main) but ONLY on drift, and it follows the
  reset with `docker restart poindexter-worker` so pending migrations apply.
  Division of labor:
    - this scheduled job: keep the clone's code == origin/main (no restart)
    - the probe: on migration drift, reset + restart the worker to APPLY it

  SAFE because the deploy clone is dedicated — nothing else ever edits it, so
  `reset --hard` can never clobber uncommitted work. Run -Install once to
  register a Windows Scheduled Task that runs this every 10 minutes.

.PARAMETER Install
  Register the recurring Scheduled Task (every 10 min) and run one sync now.

.PARAMETER Uninstall
  Remove the Scheduled Task.

.EXAMPLE
  pwsh scripts/deploy-checkout-sync.ps1            # one-shot sync
  pwsh scripts/deploy-checkout-sync.ps1 -Install   # register the 10-min task
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

# Deploy checkout dir — keep in sync with scripts/setup-deploy-checkout.sh and
# the POINDEXTER_DEPLOY_ROOT you set in .env for docker-compose.
$DeployDir = if ($env:POINDEXTER_DEPLOY_ROOT) {
    $env:POINDEXTER_DEPLOY_ROOT
} else {
    Join-Path $env:USERPROFILE '.poindexter\deploy\glad-labs-stack'
}
$SourceRemote = if ($env:SOURCE_REMOTE) { $env:SOURCE_REMOTE } else { 'origin' }
$SyncBranch = if ($env:SYNC_BRANCH) { $env:SYNC_BRANCH } else { 'main' }
$TaskName = 'Poindexter-DeployCheckoutSync'

function Write-Log($msg) { Write-Host "[deploy-checkout-sync] $msg" }

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Log "Removed scheduled task '$TaskName'."
    } else {
        Write-Log "No scheduled task '$TaskName' to remove."
    }
    return
}

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    # Prefer pwsh (PowerShell 7) if present; fall back to Windows PowerShell.
    $pwshExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $pwshExe) { $pwshExe = (Get-Command powershell).Source }

    $action = New-ScheduledTaskAction -Execute $pwshExe `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    # Every 10 minutes, indefinitely, starting at the next round minute.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 10)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Sync the Poindexter deploy checkout to origin/main (#228)' `
        -Force | Out-Null
    Write-Log "Registered scheduled task '$TaskName' (every 10 min)."
    # Fall through to run one sync immediately.
}

# ---- One sync pass --------------------------------------------------------
if (-not (Test-Path (Join-Path $DeployDir '.git'))) {
    Write-Log "ERROR: $DeployDir is not a git checkout. Run scripts/setup-deploy-checkout.sh first."
    exit 1
}

Write-Log "Syncing $DeployDir to $SourceRemote/$SyncBranch ..."
& git -C $DeployDir fetch $SourceRemote $SyncBranch --prune
if ($LASTEXITCODE -ne 0) { Write-Log "fetch failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
& git -C $DeployDir reset --hard "$SourceRemote/$SyncBranch"
if ($LASTEXITCODE -ne 0) { Write-Log "reset failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
& git -C $DeployDir clean -fd | Out-Null

$head = (& git -C $DeployDir rev-parse --short HEAD).Trim()
Write-Log "Deploy checkout now at $head ($SourceRemote/$SyncBranch)."
