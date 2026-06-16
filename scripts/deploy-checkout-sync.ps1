<#
.SYNOPSIS
  Keep the dedicated deploy checkout in sync with origin/main.

.DESCRIPTION
  Glad-Labs/poindexter#228 - companion to scripts/setup-deploy-checkout.sh.

  Once the stack's runtime code mounts are repointed at the dedicated deploy
  clone (POINDEXTER_DEPLOY_ROOT=~/.poindexter/deploy/glad-labs-stack), that
  clone is what the worker (and the other src-mounting services) actually run.
  It therefore needs to be advanced to origin/main as merges land - otherwise
  the relocated worker would run pinned clone-time code forever.

  This script does the host-side half of the genuine self-heal loop:

    git fetch origin main --prune          # network half (creds live on host)
    git reset --hard origin/main           # advance the working tree
    git clean -fd                          # wipe any stray untracked file

  ...then, when that advanced HEAD differs from the SHA the pipeline
  containers were last restarted onto, it BOUNCES the bind-mount app
  containers (poindexter-worker + poindexter-pipeline-bot by default) so the
  freshly-synced code is actually re-imported. App code is bind-mounted
  read-only into /app, so a restart - NOT a rebuild - is the deploy; only
  dependency / Dockerfile changes need `docker compose build`. Syncing the
  files alone never reloads a long-lived process that already imported the
  old modules, which is why a pure-Python merge used to sit dormant on disk
  until the next manual deploy-worker.ps1 or a migration-drift restart.

  Intentionally NOT restarted:
    - poindexter-prefect-worker: runs each flow as a fresh subprocess that
      re-imports /app per run, so new pipeline code lands on the next run
      without a bounce - and bouncing it would interrupt an in-flight post.
    - poindexter-brain-daemon: brain code is image-baked (its /app is the
      image, not a bind-mount), so a restart can't reload it - brain code
      changes need `docker compose build brain-daemon`.

  The in-brain-container migration-drift probe still does its own reset +
  `docker restart poindexter-worker` ONLY on migration drift; it stays as a
  safety net and remains the path that applies pending migrations.

  A marker file (~/.poindexter/deploy-last-restarted-sha, kept OUTSIDE the
  clone so `git clean -fd` can't wipe it) records the SHA last deployed, so a
  transient `docker restart` failure is retried next cycle instead of
  silently leaving stale code running. On first run (no marker) it records
  the current SHA WITHOUT restarting, to avoid a surprise bounce on install.

  SAFE because the deploy clone is dedicated - nothing else ever edits it, so
  `reset --hard` can never clobber uncommitted work. Run -Install once to
  register a Windows Scheduled Task that runs this every 10 minutes.

.PARAMETER Install
  Register the recurring Scheduled Task (every 10 min) and run one sync now.

.PARAMETER Uninstall
  Remove the Scheduled Task.

.PARAMETER NoRestart
  Sync the deploy clone to origin/main but never restart containers (the
  pre-2026-06 behavior: code lands on disk, reloaded only on the next manual
  deploy or migration-drift restart).

.PARAMETER RestartContainers
  Containers to bounce when new code is synced. Defaults to the two
  long-lived bind-mount app containers that need a process restart to
  re-import changed Python (poindexter-worker, poindexter-pipeline-bot).

.EXAMPLE
  pwsh scripts/deploy-checkout-sync.ps1            # one-shot sync + restart-on-change
  pwsh scripts/deploy-checkout-sync.ps1 -Install   # register the 10-min task
  pwsh scripts/deploy-checkout-sync.ps1 -NoRestart # sync only, never bounce
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$NoRestart,
    # Skip the Prefect active-run guard and reset regardless. Use for manual
    # deploys where you've confirmed no sensitive flow is mid-execution.
    [switch]$NoFlowCheck,
    [string[]]$RestartContainers = @('poindexter-worker', 'poindexter-pipeline-bot')
)

$ErrorActionPreference = 'Stop'

# Deploy checkout dir - keep in sync with scripts/setup-deploy-checkout.sh and
# the POINDEXTER_DEPLOY_ROOT you set in .env for docker-compose.
$DeployDir = if ($env:POINDEXTER_DEPLOY_ROOT) {
    $env:POINDEXTER_DEPLOY_ROOT
} else {
    Join-Path $env:USERPROFILE '.poindexter\deploy\glad-labs-stack'
}
$SourceRemote = if ($env:SOURCE_REMOTE) { $env:SOURCE_REMOTE } else { 'origin' }
$SyncBranch = if ($env:SYNC_BRANCH) { $env:SYNC_BRANCH } else { 'main' }
$PrefectApiUrl = if ($env:PREFECT_API_URL) { $env:PREFECT_API_URL } else { 'http://localhost:4200/api' }
$TaskName = 'Poindexter-DeployCheckoutSync'

function Write-Log($msg) { Write-Host "[deploy-checkout-sync] $msg" }

# Returns $true if the Prefect API reports at least one RUNNING flow run.
# Returns $false if the API is unreachable (safe default: allow the sync).
function Test-PrefectFlowRunning {
    try {
        $body = '{"flow_runs":{"state":{"type":{"any_":["RUNNING"]}}},"limit":1}'
        $resp = Invoke-RestMethod -Uri "$PrefectApiUrl/flow_runs/filter" `
            -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 3
        return ($resp.Count -gt 0)
    } catch {
        # Prefect unreachable - skip the guard, proceed with sync.
        return $false
    }
}

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

# ---- Prefect flow-run guard -----------------------------------------------
# git reset --hard rewrites working-tree files non-atomically. A Prefect flow
# spawns a fresh subprocess that re-imports /app on each run; if that import
# starts during the reset window the subprocess can read a torn file. Guard
# against this by checking for active RUNNING runs before touching the tree.
# -NoFlowCheck bypasses this for explicit manual deploys.
if (-not $NoFlowCheck -and (Test-PrefectFlowRunning)) {
    Write-Log "Active Prefect flow run detected; skipping reset this cycle to avoid mid-import file race. Will retry next cycle."
    exit 0
}

& git -C $DeployDir fetch $SourceRemote $SyncBranch --prune
if ($LASTEXITCODE -ne 0) { Write-Log "fetch failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
& git -C $DeployDir reset --hard "$SourceRemote/$SyncBranch"
if ($LASTEXITCODE -ne 0) { Write-Log "reset failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
& git -C $DeployDir clean -fd | Out-Null

$head = (& git -C $DeployDir rev-parse HEAD).Trim()
$shortHead = $head.Substring(0, 9)
Write-Log "Deploy checkout now at $shortHead ($SourceRemote/$SyncBranch)."

# ---- Restart-on-change ----------------------------------------------------
# Updating the bind-mounted files does NOT reload the long-lived worker
# processes that already imported the old modules; they need a restart to
# re-import. Bounce them only when the synced HEAD differs from the SHA the
# containers were last restarted onto, recorded in a marker file kept OUTSIDE
# the clone so `git clean -fd` above can't wipe it. Disabled with -NoRestart.
if ($NoRestart) {
    Write-Log "-NoRestart set; code synced on disk, containers left as-is."
    return
}

$markerFile = Join-Path $env:USERPROFILE '.poindexter\deploy-last-restarted-sha'
$lastDeployed = if (Test-Path $markerFile) { (Get-Content $markerFile -Raw).Trim() } else { '' }

if (-not $lastDeployed) {
    # First run after install: assume the running containers are already
    # current; record a baseline so we don't bounce them on this pass.
    Set-Content -Path $markerFile -Value $head -NoNewline
    Write-Log "No prior deploy marker; recorded baseline $shortHead without restarting."
    return
}

if ($lastDeployed -eq $head) {
    Write-Log "Containers already on $shortHead; nothing to restart."
    return
}

$lastShort = if ($lastDeployed.Length -ge 9) { $lastDeployed.Substring(0, 9) } else { $lastDeployed }
Write-Log "Code advanced $lastShort -> $shortHead; restarting: $($RestartContainers -join ', ')"

$failed = @()
foreach ($c in $RestartContainers) {
    # Skip containers that aren't present (e.g. a deliberately-stopped bot) so
    # one missing container can't trap us into bouncing the others every cycle.
    & docker container inspect $c *> $null
    if ($LASTEXITCODE -ne 0) { Write-Log "  skip '$c' (not present)"; continue }
    & docker restart $c | Out-Null
    if ($LASTEXITCODE -ne 0) { $failed += $c; Write-Log "  FAILED to restart '$c' (exit $LASTEXITCODE)" }
    else { Write-Log "  restarted '$c'" }
}

if ($failed.Count -gt 0) {
    Write-Log "WARNING: $($failed.Count) container(s) failed to restart ($($failed -join ', ')); NOT recording marker - will retry next cycle."
    exit 1
}

# Record only after a clean restart so a transient failure retries next cycle.
Set-Content -Path $markerFile -Value $head -NoNewline
Write-Log "Pipeline now running $shortHead."
