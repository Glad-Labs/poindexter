<#
.SYNOPSIS
    Bring the local Poindexter worker stack up to origin/main - the canonical
    "deploy" for the self-hosted worker/brain.

.DESCRIPTION
    The worker, pipeline-bot and prefect-worker containers BIND-MOUNT the
    dedicated deploy clone (POINDEXTER_DEPLOY_ROOT, defaulting to
    ~/.poindexter/deploy/glad-labs-stack) - that clone is what the running
    pipeline actually executes, NOT this dev checkout. The brain-daemon is the
    one EXCEPTION: its code is baked into its image (the ./brain mount was
    removed in Glad-Labs/poindexter#456 because the brain's bare-name imports
    resolve against the image-baked /app, not the mount), so it is REBUILT,
    not restarted - a restart re-runs the old image and ships nothing.
    Fast-forwarding only the dev checkout was the root cause of the long-running
    "merged != deployed" drift; this script syncs the deploy clone too.

    This script makes the deploy a single guarded command:
      1. Refuses to run with a dirty working tree (so it never clobbers WIP).
      2. Tag-backs-up the current branch tip if it carries unpushed commits.
      3. Fetches, checks out main, fast-forwards to origin/main.
      3b. Syncs the deploy clone to origin/main via deploy-checkout-sync.ps1
          (the deploy clone is what the containers bind-mount and run).
      4. Verifies both the dev checkout AND the deploy clone HEAD == origin/main.
      5. Reloads the pipeline containers: restarts the bind-mounted worker
         family, REBUILDS the image-baked brain-daemon (via start-stack.sh).
      6. Waits for the worker AND brain healthchecks to go healthy and confirms
         poindexter_worker_up=1 via Prometheus.

    Bind-mounted app code (the worker family) goes live on a restart - no image
    rebuild. The brain-daemon's baked code needs a `docker compose build
    brain-daemon`, which this script runs through start-stack.sh so the
    bootstrap.toml secrets resolve and COMPOSE_PROJECT_NAME stays pinned. Other
    base-image/dependency changes still need a manual `docker compose build`.

.PARAMETER Force
    Proceed even if the working tree has uncommitted changes (they are stashed
    with a labelled stash first). Use sparingly.

.PARAMETER SkipRestart
    Update the checkout to main but do not restart containers (dry-run-ish).

.PARAMETER Containers
    Pipeline containers to reload. Defaults to the worker family (restarted -
    bind-mounted code) plus poindexter-brain-daemon (rebuilt - image-baked).

.EXAMPLE
    pwsh ./scripts/deploy-worker.ps1
    # Standard deploy: host -> main, sync deploy clone, restart pipeline containers, verify health.

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

# Resolve Git Bash explicitly. On Windows the PATH `bash` is usually WSL's
# C:\Windows\System32\bash.exe, which runs in a separate filesystem namespace and
# cannot see Docker or the C:\ paths the stack uses. git IS on PATH (this script
# shells out to it), so derive Git Bash from git's location
# (<GitRoot>\cmd\git.exe -> <GitRoot>\bin\bash.exe); fall back to standard installs.
# Mirrors scripts/deploy-checkout-sync.ps1 + scripts/docker-watchdog.ps1.
function Resolve-GitBash {
    $git = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($git) {
        $cand = Join-Path (Split-Path (Split-Path $git)) 'bin\bash.exe'
        if (Test-Path $cand) { return $cand }
    }
    foreach ($p in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return 'bash'  # last resort; a failed rebuild surfaces if this is WSL bash
}

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

# 4. Verify dev checkout HEAD == origin/main.
$head = (git rev-parse HEAD)
$origin = (git rev-parse origin/main)
if ($head -ne $origin) { Fail "HEAD ($head) != origin/main ($origin) after pull - not fast-forwardable. Resolve manually." }
Ok "checkout on main @ $($head.Substring(0,9)) == origin/main"

# 3b. Sync the deploy clone to origin/main (containers bind-mount this tree).
Info "syncing deploy clone to origin/main..."
$syncScript = Join-Path $PSScriptRoot 'deploy-checkout-sync.ps1'
if (Test-Path $syncScript) {
    & pwsh $syncScript
    Ok "deploy clone synced"
} else {
    Fail "deploy-checkout-sync.ps1 not found at $syncScript - run scripts/setup-deploy-checkout.sh first"
}

# 4b. Verify the deploy clone HEAD also matches origin/main.
$deployRoot = if ($env:POINDEXTER_DEPLOY_ROOT) { $env:POINDEXTER_DEPLOY_ROOT } else { Join-Path $env:USERPROFILE '.poindexter\deploy\glad-labs-stack' }
$deployHead = $null
if (Test-Path (Join-Path $deployRoot '.git')) {
    $deployHead = (git -C $deployRoot rev-parse HEAD 2>$null)
    if ($deployHead -ne $origin) {
        Fail "deploy clone HEAD ($deployHead) != origin/main ($origin) after sync - check deploy-checkout-sync.ps1"
    }
    Ok "deploy clone on main @ $($deployHead.Substring(0,9)) == origin/main"
}

if ($SkipRestart) { Info "-SkipRestart set - checkout updated, leaving containers as-is."; exit 0 }

# 5. Reload the pipeline containers.
#    The worker family bind-mounts its app code from the deploy clone, so a
#    plain `docker restart` reloads their .py edits. The brain-daemon is the
#    EXCEPTION: its code is baked into the image (the ./brain mount was removed
#    in Glad-Labs/poindexter#456), so a restart re-runs the OLD image. It must
#    be REBUILT. Route the rebuild through start-stack.sh so the bootstrap.toml
#    secrets resolve (the compose file aborts on ${SECRET:?} sentinels with no
#    deploy-dir .env) and COMPOSE_PROJECT_NAME stays pinned (a bare
#    `docker compose` from the deploy dir would fork a divergent project and
#    orphan the data volumes).
$brainContainer = 'poindexter-brain-daemon'
$restartTargets = @($Containers | Where-Object { $_ -ne $brainContainer })
$rebuildBrain   = $Containers -contains $brainContainer

if ($rebuildBrain) {
    $startStack = (Join-Path $deployRoot 'scripts/start-stack.sh') -replace '\\', '/'
    if (-not (Test-Path $startStack)) {
        Fail "start-stack.sh not found at $startStack - cannot rebuild the image-baked brain. Run scripts/setup-deploy-checkout.sh first."
    }
    $bash = Resolve-GitBash
    Info "rebuilding brain-daemon image (baked code - a restart alone ships nothing)..."
    & $bash $startStack up -d --build brain-daemon
    if ($LASTEXITCODE -ne 0) { Fail "brain-daemon rebuild failed (start-stack.sh via '$bash' exit $LASTEXITCODE) - check 'docker logs poindexter-brain-daemon'. If '$bash' is WSL bash, install Git Bash." }
    Ok "brain-daemon rebuilt + recreated"
}

if ($restartTargets.Count -gt 0) {
    Info "restarting (bind-mounted code): $($restartTargets -join ', ')"
    docker restart @restartTargets | Out-Null
}

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

# 6b. Confirm the REBUILT brain-daemon came back healthy. Its code is the thing
#     we just swapped, so verify it rather than trust the build exit code.
if ($rebuildBrain) {
    Info "waiting for poindexter-brain-daemon healthcheck..."
    $brainHealthy = $false
    for ($i = 1; $i -le 24; $i++) {
        Start-Sleep -Seconds 5
        $bstatus = (docker inspect poindexter-brain-daemon --format '{{.State.Health.Status}}' 2>$null)
        Write-Host "  [$i] brain health=$bstatus"
        if ($bstatus -eq 'healthy') { $brainHealthy = $true; break }
    }
    if (-not $brainHealthy) { Fail "brain-daemon did not reach 'healthy' in ~2 min after rebuild - check 'docker logs poindexter-brain-daemon'." }
    Ok "brain-daemon healthy on the rebuilt image"
}

# Best-effort: confirm the Prometheus liveness gauge agrees.
try {
    $resp = Invoke-RestMethod -Uri 'http://localhost:9091/api/v1/query?query=poindexter_worker_up' -TimeoutSec 5
    $val = $resp.data.result[0].value[1]
    if ($val -eq '1') { Ok "poindexter_worker_up=1" } else { Info "poindexter_worker_up=$val (gauge may lag a scrape)" }
} catch {
    Info "Prometheus liveness check skipped ($($_.Exception.Message))"
}

$reportSha = if ($deployHead) { $deployHead } else { $head }
Ok "deploy complete - prod is on origin/main @ $($reportSha.Substring(0,9))"
