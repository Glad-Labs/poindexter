<#
.SYNOPSIS
    Bring the local Poindexter worker stack up to origin/main - the canonical
    "deploy" for the self-hosted worker/brain.

.DESCRIPTION
    The worker, brain, pipeline-bot and prefect-worker containers BIND-MOUNT
    this repository's working tree (src/cofounder_agent -> /app, brain ->
    /opt/poindexter/brain). That means the running pipeline executes whatever
    branch this checkout happens to be sitting on - so if the checkout is left
    on a feature branch, merged work on main never reaches production. This was
    the root cause of the long-running "merged != deployed" drift.

    This script makes the deploy a single guarded command:
      1. Refuses to run with a dirty working tree (so it never clobbers WIP).
      2. Tag-backs-up the current branch tip if it carries unpushed commits.
      3. Fetches, checks out main, fast-forwards to origin/main.
      4. Verifies HEAD == origin/main (aborts otherwise).
      5. Restarts the pipeline containers so they reload the bind-mounted code.
      6. Waits for the worker healthcheck to go healthy and confirms
         poindexter_worker_up=1 via Prometheus.

    It does NOT rebuild any image - app code is bind-mounted, so a restart is
    the deploy. Base-image/dependency changes still need `docker compose build`.

.PARAMETER Force
    Proceed even if the working tree has uncommitted changes (they are stashed
    with a labelled stash first). Use sparingly.

.PARAMETER SkipRestart
    Update the checkout to main but do not restart containers (dry-run-ish).

.PARAMETER Containers
    Containers to restart. Defaults to the four that bind-mount app/brain code.

.EXAMPLE
    pwsh ./scripts/deploy-worker.ps1
    # Standard deploy: host -> main, restart pipeline containers, verify health.

.NOTES
    Companion to a deploy-drift canary that alerts when this checkout falls
    behind origin/main so the drift can't hide between deploys.
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipRestart,
    [string[]]$Containers = @(
        'poindexter-worker',
        'poindexter-brain-daemon',
        'poindexter-pipeline-bot',
        'poindexter-prefect-worker'
    )
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host "[deploy-worker] ABORT: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host "[deploy-worker] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[deploy-worker] OK: $msg" -ForegroundColor Green }

# Repo root = parent of the scripts/ dir this file lives in.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Info "repo: $RepoRoot"

# 1. Working-tree cleanliness - never clobber WIP.
$dirty = git status --porcelain=v1
if ($dirty) {
    if ($Force) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Info "working tree dirty + -Force: stashing as deploy-worker-wip-$stamp"
        git stash push -u -m "deploy-worker-wip-$stamp" | Out-Null
    } else {
        Fail "working tree has uncommitted changes. Commit/stash them, or re-run with -Force. `n$dirty"
    }
}

# 2. Back up the current branch tip if it has commits not on origin/main.
git fetch origin --quiet
$curBranch = (git branch --show-current)
if ($curBranch -and $curBranch -ne 'main') {
    $ahead = (git rev-list --count "origin/main..HEAD" 2>$null)
    if ($ahead -and [int]$ahead -gt 0) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $tag = "backup/pre-deploy-$stamp"
        git tag -f $tag HEAD | Out-Null
        Info "current branch '$curBranch' has $ahead commit(s) not on main - tagged $tag (push a PR if you want them)"
    }
}

# 3. Checkout main + fast-forward.
Info "checking out main + fast-forwarding to origin/main"
git checkout main | Out-Null
git pull --ff-only origin main | Out-Null

# 4. Verify HEAD == origin/main.
$head = (git rev-parse HEAD)
$origin = (git rev-parse origin/main)
if ($head -ne $origin) { Fail "HEAD ($head) != origin/main ($origin) after pull - not fast-forwardable. Resolve manually." }
Ok "checkout on main @ $($head.Substring(0,9)) == origin/main"

if ($SkipRestart) { Info "-SkipRestart set - checkout updated, leaving containers as-is."; exit 0 }

# 5. Restart the pipeline containers (reloads bind-mounted code).
Info "restarting: $($Containers -join ', ')"
docker restart @Containers | Out-Null

# 6. Wait for the worker healthcheck + confirm worker_up.
Info "waiting for poindexter-worker healthcheck..."
$healthy = $false
for ($i = 1; $i -le 24; $i++) {
    Start-Sleep -Seconds 5
    $status = (docker inspect poindexter-worker --format '{{.State.Health.Status}}' 2>$null)
    Write-Host "  [$i] worker health=$status"
    if ($status -eq 'healthy') { $healthy = $true; break }
}
if (-not $healthy) { Fail "worker did not reach 'healthy' in ~2 min - check 'docker logs poindexter-worker'." }

# Best-effort: confirm the Prometheus liveness gauge agrees.
try {
    $resp = Invoke-RestMethod -Uri 'http://localhost:9091/api/v1/query?query=poindexter_worker_up' -TimeoutSec 5
    $val = $resp.data.result[0].value[1]
    if ($val -eq '1') { Ok "poindexter_worker_up=1" } else { Info "poindexter_worker_up=$val (gauge may lag a scrape)" }
} catch {
    Info "Prometheus liveness check skipped ($($_.Exception.Message))"
}

Ok "deploy complete - prod is on origin/main @ $($head.Substring(0,9))"
