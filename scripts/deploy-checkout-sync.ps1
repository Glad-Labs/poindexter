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

  OBSERVABILITY (Glad-Labs/glad-labs-stack). When this task runs under the
  Scheduler it runs hidden + non-interactive, so its `Write-Host` narration
  goes nowhere and the Windows TaskScheduler/Operational history log is
  disabled by default - leaving a green-looking 0x0 task with no on-disk
  proof it ever did anything ("appears broken" even when it isn't). So every
  run now also:
    - appends timestamped lines to ~/.poindexter/deploy-checkout-sync.log
      (rotated to .log.1 past POINDEXTER_DEPLOY_LOG_MAX_BYTES, default 5MB),
      capturing the git fetch/reset/clean and docker restart output that the
      hidden task used to discard; and
    - writes a one-object machine-readable status to
      ~/.poindexter/deploy-checkout-sync.status.json (result, head,
      previousHead, restarted[], detail, timestamp) - the substrate a Grafana
      panel / phone check can read. `result` is one of: deployed |
      synced-no-change | synced-norestart | baseline-recorded | flow-gap-skip
      | error.
  Run `-Status` for a one-shot operator health view (task state + last status
  + log tail); run `-SelfTest` to exercise the logging/rotation/status
  plumbing against a temp dir (no git/docker) and exit 0/1.

  SAFE because the deploy clone is dedicated - nothing else ever edits it, so
  `reset --hard` can never clobber uncommitted work. Run -Install once to
  register a Windows Scheduled Task that runs this every 10 minutes.

.PARAMETER Install
  Register the recurring Scheduled Task (every 10 min) and run one sync now.

.PARAMETER Uninstall
  Remove the Scheduled Task.

.PARAMETER Status
  Print a one-shot operator health view - the Scheduled Task state, last run
  result, the deploy clone HEAD, the last status JSON, and the tail of the
  log - then exit. Read-only; touches no containers and does not sync.

.PARAMETER SelfTest
  Run deterministic assertions over the logging / rotation / status-writing
  helpers against a throwaway temp dir (no git, docker, or Prefect), print
  PASS/FAIL per check, and exit 0 (all passed) or 1 (any failed). This is the
  runnable test that ships with the observability change.

.PARAMETER NoRestart
  Sync the deploy clone to origin/main but never restart containers (the
  pre-2026-06 behavior: code lands on disk, reloaded only on the next manual
  deploy or migration-drift restart).

.PARAMETER NoFlowCheck
  Skip the Prefect active-run guard and reset regardless. Use for manual
  deploys where you've confirmed no sensitive flow is mid-execution.

.PARAMETER RestartContainers
  Containers to bounce when new code is synced. Defaults to the two
  long-lived bind-mount app containers that need a process restart to
  re-import changed Python (poindexter-worker, poindexter-pipeline-bot).

.EXAMPLE
  pwsh scripts/deploy-checkout-sync.ps1            # one-shot sync + restart-on-change
  pwsh scripts/deploy-checkout-sync.ps1 -Install   # register the 10-min task
  pwsh scripts/deploy-checkout-sync.ps1 -NoRestart # sync only, never bounce
  pwsh scripts/deploy-checkout-sync.ps1 -Status    # is it actually working?
  pwsh scripts/deploy-checkout-sync.ps1 -SelfTest  # test the logging plumbing

.NOTES
  Log    : %USERPROFILE%\.poindexter\deploy-checkout-sync.log (+ .log.1 backup)
  Status : %USERPROFILE%\.poindexter\deploy-checkout-sync.status.json
  Marker : %USERPROFILE%\.poindexter\deploy-last-restarted-sha
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$SelfTest,
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

# Observability artifacts (hidden scheduled runs discard Write-Host + the
# TaskScheduler history log is disabled, so persist proof-of-work to disk).
$LogFile = Join-Path $env:USERPROFILE '.poindexter\deploy-checkout-sync.log'
$StatusFile = Join-Path $env:USERPROFILE '.poindexter\deploy-checkout-sync.status.json'
$LogMaxBytes = if ($env:POINDEXTER_DEPLOY_LOG_MAX_BYTES) { [int]$env:POINDEXTER_DEPLOY_LOG_MAX_BYTES } else { 5MB }

# Append a timestamped, levelled line to the deploy log AND echo to the host
# (the echo is for interactive runs; the file is what survives a hidden task
# run). Logging must never abort the deploy, so a file-write failure only
# warns to the host.
function Write-Log {
    param([Parameter(Mandatory)][string]$Message, [string]$Level = 'INFO')
    Write-Host "[deploy-checkout-sync] $Message"
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message"
    try { Add-Content -Path $LogFile -Value $line -ErrorAction Stop }
    catch { Write-Host "[deploy-checkout-sync] WARN: could not write log file ${LogFile}: $_" }
}

# Single-backup size rotation: when the live log reaches the cap, move it to
# .log.1 (overwriting any prior backup) so the next Write-Log starts fresh.
# Bounded growth without a cron - the task itself rolls the file.
function Invoke-LogRotation {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][int]$MaxBytes)
    try {
        if ((Test-Path $Path) -and ((Get-Item $Path).Length -ge $MaxBytes)) {
            Move-Item -Path $Path -Destination "$Path.1" -Force
        }
    } catch { Write-Host "[deploy-checkout-sync] WARN: log rotation failed: $_" }
}

# Write the machine-readable last-run status. One compact JSON object so a
# Grafana textfile collector / phone check can read the latest outcome without
# parsing the human log. Never aborts the deploy on a write failure.
function Write-DeployStatus {
    param(
        [Parameter(Mandatory)][ValidateSet('deployed', 'synced-no-change', 'synced-norestart', 'baseline-recorded', 'flow-gap-skip', 'error')]
        [string]$Result,
        [string]$Head = '',
        [string]$PreviousHead = '',
        [string[]]$Restarted = @(),
        [string]$Detail = ''
    )
    $obj = [ordered]@{
        timestamp    = (Get-Date).ToString('o')
        result       = $Result
        head         = $Head
        previousHead = $PreviousHead
        restarted    = @($Restarted)
        detail       = $Detail
        host         = $env:COMPUTERNAME
    }
    try { ($obj | ConvertTo-Json -Compress) | Set-Content -Path $StatusFile -Encoding UTF8 -ErrorAction Stop }
    catch { Write-Host "[deploy-checkout-sync] WARN: could not write status file ${StatusFile}: $_" }
}

# Operator health view: answer "is this actually working?" at a glance.
function Show-DeployStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host "$TaskName - $($task.State)"
        if ($info) {
            Write-Host ("  Last run : {0}  (result {1})" -f $info.LastRunTime, $info.LastTaskResult)
            Write-Host ("  Next run : {0}" -f $info.NextRunTime)
            Write-Host ("  Missed   : {0}" -f $info.NumberOfMissedRuns)
        }
    } else {
        Write-Host "$TaskName - NOT REGISTERED (install with: -Install)"
    }
    if (Test-Path (Join-Path $DeployDir '.git')) {
        Write-Host ("  Clone HEAD : {0}" -f ((& git -C $DeployDir rev-parse --short HEAD) 2>$null))
    } else {
        Write-Host "  Clone HEAD : (deploy clone not found at $DeployDir)"
    }
    if (Test-Path $StatusFile) {
        Write-Host "  --- last status ($StatusFile) ---"
        (Get-Content $StatusFile -Raw | ConvertFrom-Json | ConvertTo-Json) -split "`r?`n" | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  (no status file yet at $StatusFile)"
    }
    if (Test-Path $LogFile) {
        Write-Host "  --- last 15 log lines ($LogFile) ---"
        Get-Content $LogFile -Tail 15 | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  (no log yet at $LogFile)"
    }
}

# Deterministic checks of the logging plumbing against a temp dir - no git,
# docker, or Prefect. Returns 0 (all passed) / 1 (any failed). This is the
# runnable test for the observability change; the repo has no Pester harness.
function Invoke-SelfTest {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('dcs-selftest-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $results = [System.Collections.Generic.List[bool]]::new()
    function Test-Case([string]$Name, [bool]$Cond) {
        if ($Cond) { Write-Host "  PASS  $Name" } else { Write-Host "  FAIL  $Name" }
        $results.Add($Cond)
    }
    Write-Host '[deploy-checkout-sync] self-test:'
    try {
        # 1) Write-Log appends a timestamped, levelled line.
        $script:LogFile = Join-Path $tmp 'test.log'
        Write-Log 'hello world' 'INFO'
        $c = Get-Content $script:LogFile -Raw
        Test-Case 'Write-Log creates the file'        (Test-Path $script:LogFile)
        Test-Case 'Write-Log records the message'      ($c -match 'hello world')
        Test-Case 'Write-Log tags the level'           ($c -match '\[INFO\]')
        Test-Case 'Write-Log timestamps the line'      ($c -match '\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')

        # 2) Rotation: no-op under cap; rolls to .1 and starts fresh at/over cap.
        Invoke-LogRotation -Path $script:LogFile -MaxBytes 1MB
        Test-Case 'no rotation while under cap'        (-not (Test-Path "$($script:LogFile).1"))
        Set-Content -Path $script:LogFile -Value ('x' * 4096) -NoNewline
        Invoke-LogRotation -Path $script:LogFile -MaxBytes 1024
        Test-Case 'rotates to .1 when over cap'        (Test-Path "$($script:LogFile).1")
        Test-Case 'live log reset after rotation'      (-not (Test-Path $script:LogFile))

        # 3) Status JSON round-trips with the documented fields.
        $script:StatusFile = Join-Path $tmp 'test.status.json'
        Write-DeployStatus -Result 'deployed' -Head 'abc1234' -PreviousHead 'def5678' -Restarted @('poindexter-worker') -Detail 'unit'
        $st = Get-Content $script:StatusFile -Raw | ConvertFrom-Json
        Test-Case 'status result field'                ($st.result -eq 'deployed')
        Test-Case 'status head field'                  ($st.head -eq 'abc1234')
        Test-Case 'status previousHead field'          ($st.previousHead -eq 'def5678')
        Test-Case 'status restarted[] field'           (@($st.restarted) -contains 'poindexter-worker')
        Test-Case 'status carries a timestamp'         ([bool]$st.timestamp)

        # 4) Compose-apply command is host-side bash + clone start-stack + no-build.
        $applyArgv = Get-ComposeApplyCommand -DeployDir 'C:\fake\clone'
        Test-Case 'apply uses git bash (not WSL)'      (($applyArgv[0] -eq 'bash') -or ($applyArgv[0] -match 'bash\.exe$'))
        Test-Case 'apply targets clone start-stack'    ($applyArgv[1] -eq 'C:\fake\clone/scripts/start-stack.sh')
        Test-Case 'apply passes up -d --no-build'      (($applyArgv[2..4] -join ' ') -eq 'up -d --no-build')
    } finally {
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    $failed = @($results | Where-Object { -not $_ }).Count
    if ($failed -eq 0) { Write-Host "[deploy-checkout-sync] SELF-TEST PASSED ($($results.Count) checks)"; return 0 }
    Write-Host "[deploy-checkout-sync] SELF-TEST FAILED ($failed of $($results.Count) checks)"; return 1
}

# Run a native command, routing BOTH stdout and stderr line-by-line into the
# deploy log, and return its exit code. git/docker write normal progress to
# stderr, so `2>&1` is needed to capture it - but under EAP=Stop that promotes
# the first stderr line to a terminating error (NativeCommandError), which would
# turn a healthy fetch-with-updates into a bogus failure. So drop to
# EAP=Continue for the duration; the caller decides what a non-zero code means.
function Invoke-Logged {
    param([Parameter(Mandatory)][string]$Exe, [Parameter(Mandatory)][string[]]$ArgList, [string]$Tag = 'cmd')
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @ArgList 2>&1 | ForEach-Object { Write-Log "$Tag | $_" }
    } finally {
        $ErrorActionPreference = $prev
    }
    return $LASTEXITCODE
}

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

# Resolve Git Bash explicitly. On Windows the PATH `bash` is usually WSL's
# C:\Windows\System32\bash.exe, which runs in a separate filesystem namespace and
# cannot see Docker or the C:\ paths the stack uses. git IS on PATH (this script
# shells out to it), so derive Git Bash from git's location
# (<GitRoot>\cmd\git.exe -> <GitRoot>\bin\bash.exe); fall back to standard installs.
function Resolve-GitBash {
    $git = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($git) {
        $cand = Join-Path (Split-Path (Split-Path $git)) 'bin\bash.exe'
        if (Test-Path $cand) { return $cand }
    }
    foreach ($p in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return 'bash'  # last resort; a failed apply surfaces if this is WSL bash
}

# Build the host-side "apply the clone's compose" command. Runs the CLONE's
# start-stack.sh (the canonical launcher: exports bootstrap.toml secrets, regenerates
# the grafana/offsite runtime env files, pins COMPOSE_PROJECT_NAME, cd's into the
# clone), so `docker compose up -d --no-build` recreates exactly the services whose
# compose stanza changed. Invoked via Git Bash (NOT the PATH `bash`, which is WSL).
# --no-build: never rebuild here; Dockerfile / build-context changes are separate.
function Get-ComposeApplyCommand {
    param([Parameter(Mandatory)][string]$DeployDir)
    return @((Resolve-GitBash), "$DeployDir/scripts/start-stack.sh", 'up', '-d', '--no-build')
}

# ---- Read-only / test modes (no rotation, no sync, no restart) ------------
if ($SelfTest) { exit (Invoke-SelfTest) }
if ($Status) { Show-DeployStatus; return }

# Roll the log once per real invocation so it can't grow without bound.
Invoke-LogRotation -Path $LogFile -MaxBytes $LogMaxBytes

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

    # Wrap in run-hidden.vbs (SW_HIDE at CreateProcess level) so spawned child
    # processes (git.exe, bash.exe, python.exe) never flash a console window.
    # A plain -WindowStyle Hidden powershell.exe still allocates a console object
    # that child CUI executables can un-hide briefly via console API calls.
    # The VBS lives outside the git checkout so the sync cleanup pass cannot wipe it.
    $vbsPath = Join-Path $env:USERPROFILE '.poindexter\run-hidden.vbs'
    if (-not (Test-Path $vbsPath)) {
        Set-Content -Path $vbsPath -Encoding ASCII -Value @'
Set objShell = CreateObject("WScript.Shell")
objShell.Run WScript.Arguments(0), 0, False
'@
        Write-Log "Created $vbsPath"
    }
    $psCmd   = "$pwshExe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File $scriptPath"
    $action  = New-ScheduledTaskAction -Execute 'wscript.exe' `
        -Argument "`"$vbsPath`" `"$psCmd`""
    # A daily anchor whose repetition fires every 10 min for 24h = a continuous
    # 10-minute cadence. This two-trigger composition is the robust idiom: a lone
    # -Once trigger's RepetitionDuration default varies by build, and on builds
    # where it defaults to a finite duration the task silently STOPS repeating
    # after it - the deploy sync would "appear broken" with no signal. The daily
    # anchor re-arms the 24h repetition window each day so the cadence never ends.
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).Date.AddHours(3)
    $trigger.Repetition = (
        New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes 10) `
            -RepetitionDuration (New-TimeSpan -Days 1)
    ).Repetition
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Sync the Poindexter deploy checkout to origin/main (#228)' `
        -Force | Out-Null
    Write-Log "Registered scheduled task '$TaskName' (every 10 min)."
    # Fall through to run one sync immediately.
}

# ---- One sync pass --------------------------------------------------------
try {
    if (-not (Test-Path (Join-Path $DeployDir '.git'))) {
        Write-Log "ERROR: $DeployDir is not a git checkout. Run scripts/setup-deploy-checkout.sh first." 'ERROR'
        Write-DeployStatus -Result 'error' -Detail "deploy dir is not a git checkout: $DeployDir"
        exit 1
    }

    Write-Log "Syncing $DeployDir to $SourceRemote/$SyncBranch ..."

    # ---- Prefect flow-run guard (bounded wait-for-gap) --------------------
    # git reset --hard rewrites working-tree files non-atomically. A Prefect flow
    # spawns a fresh subprocess that re-imports /app on each run; if that import
    # starts during the reset window the subprocess can read a torn file. So only
    # reset in a gap between flow runs.
    #
    # The original guard SKIPPED the whole cycle on any RUNNING flow ("retry next
    # cycle"). That silently failed once content_generation_flow moved to a ~2-min
    # cadence on even-minute marks: the 10-min sync fired on even minutes too, so
    # every sync landed inside a ~14s flow and skipped *every* time, leaving the
    # deploy clone (and prod) arbitrarily stale behind a green-looking, 0x0 task.
    # content flows are short (~14s) with ~100s gaps, so instead of skipping we
    # WAIT briefly for the in-flight flow to clear, then reset in the gap. We only
    # skip if still blocked after the cap (a genuinely stuck/long flow), where
    # deferring to the next cycle is correct. Cap is tunable via
    # SYNC_FLOW_WAIT_MAX_SEC (default 90s; fits the task's 5-min execution limit).
    # -NoFlowCheck bypasses the wait entirely for explicit manual deploys.
    if (-not $NoFlowCheck) {
        $maxWaitSec = if ($env:SYNC_FLOW_WAIT_MAX_SEC) { [int]$env:SYNC_FLOW_WAIT_MAX_SEC } else { 90 }
        $waited = 0
        while ((Test-PrefectFlowRunning) -and ($waited -lt $maxWaitSec)) {
            Write-Log "Active Prefect flow run; waiting for a gap before reset (${waited}/${maxWaitSec}s)..."
            Start-Sleep -Seconds 5
            $waited += 5
        }
        if (Test-PrefectFlowRunning) {
            Write-Log "Flow still RUNNING after ${maxWaitSec}s (stuck/long run?); skipping reset this cycle, will retry next." 'WARN'
            Write-DeployStatus -Result 'flow-gap-skip' -Detail "flow still RUNNING after ${maxWaitSec}s; deferred to next cycle"
            exit 0
        }
        if ($waited -gt 0) { Write-Log "Flow gap reached after ${waited}s; proceeding with sync." }
    }

    $rc = Invoke-Logged 'git' @('-C', $DeployDir, 'fetch', $SourceRemote, $SyncBranch, '--prune') 'git fetch'
    if ($rc -ne 0) {
        Write-Log "fetch failed (exit $rc)" 'ERROR'
        Write-DeployStatus -Result 'error' -Detail "git fetch failed (exit $rc)"
        exit $rc
    }
    $rc = Invoke-Logged 'git' @('-C', $DeployDir, 'reset', '--hard', "$SourceRemote/$SyncBranch") 'git reset'
    if ($rc -ne 0) {
        Write-Log "reset failed (exit $rc)" 'ERROR'
        Write-DeployStatus -Result 'error' -Detail "git reset --hard failed (exit $rc)"
        exit $rc
    }
    $null = Invoke-Logged 'git' @('-C', $DeployDir, 'clean', '-fd') 'git clean'

    $head = (& git -C $DeployDir rev-parse HEAD).Trim()
    $shortHead = $head.Substring(0, 9)
    Write-Log "Deploy checkout now at $shortHead ($SourceRemote/$SyncBranch)."

    # ---- Restart-on-change ------------------------------------------------
    # Updating the bind-mounted files does NOT reload the long-lived worker
    # processes that already imported the old modules; they need a restart to
    # re-import. Bounce them only when the synced HEAD differs from the SHA the
    # containers were last restarted onto, recorded in a marker file kept OUTSIDE
    # the clone so `git clean -fd` above can't wipe it. Disabled with -NoRestart.
    if ($NoRestart) {
        Write-Log "-NoRestart set; code synced on disk, containers left as-is."
        Write-DeployStatus -Result 'synced-norestart' -Head $head
        return
    }

    $markerFile = Join-Path $env:USERPROFILE '.poindexter\deploy-last-restarted-sha'
    $lastDeployed = if (Test-Path $markerFile) { (Get-Content $markerFile -Raw).Trim() } else { '' }

    if (-not $lastDeployed) {
        # First run after install: assume the running containers are already
        # current; record a baseline so we don't bounce them on this pass.
        Set-Content -Path $markerFile -Value $head -NoNewline
        Write-Log "No prior deploy marker; recorded baseline $shortHead without restarting."
        Write-DeployStatus -Result 'baseline-recorded' -Head $head
        return
    }

    if ($lastDeployed -eq $head) {
        Write-Log "Containers already on $shortHead; nothing to restart."
        Write-DeployStatus -Result 'synced-no-change' -Head $head
        return
    }

    $lastShort = if ($lastDeployed.Length -ge 9) { $lastDeployed.Substring(0, 9) } else { $lastDeployed }
    Write-Log "Code advanced $lastShort -> $shortHead; restarting: $($RestartContainers -join ', ')"

    # Apply compose/infra changes FIRST: recreate the services whose compose stanza
    # changed (mounts/env/ports/image) by running the clone's compose up. Compose
    # only touches changed services, so this is a no-op for code-only merges. The
    # restart loop below still reloads code in the long-lived bind-mount containers,
    # whose stanza is unchanged by a pure-Python edit (the source path is identical,
    # only the file content differs). Without this step a compose-only merge (e.g.
    # the GPU /root->/home/appuser mount fixes) lands on disk but never reaches the
    # running stack. Failure aborts before recording the marker so it retries.
    $applyArgv = Get-ComposeApplyCommand -DeployDir $DeployDir
    Write-Log "Applying compose from clone: $($applyArgv -join ' ')"
    $applyRc = Invoke-Logged $applyArgv[0] $applyArgv[1..($applyArgv.Length - 1)] 'compose-apply'
    if ($applyRc -ne 0) {
        Write-Log "compose-apply failed (exit $applyRc); NOT recording marker - will retry next cycle." 'ERROR'
        Write-DeployStatus -Result 'error' -Head $head -PreviousHead $lastDeployed -Detail "compose-apply failed (exit $applyRc)"
        exit 1
    }

    $failed = @()
    $restarted = @()
    foreach ($c in $RestartContainers) {
        # Skip containers that aren't present (e.g. a deliberately-stopped bot) so
        # one missing container can't trap us into bouncing the others every cycle.
        & docker container inspect $c *> $null
        if ($LASTEXITCODE -ne 0) { Write-Log "  skip '$c' (not present)"; continue }
        $rc = Invoke-Logged 'docker' @('restart', $c) 'docker'
        if ($rc -ne 0) { $failed += $c; Write-Log "  FAILED to restart '$c' (exit $rc)" 'ERROR' }
        else { $restarted += $c; Write-Log "  restarted '$c'" }
    }

    if ($failed.Count -gt 0) {
        Write-Log "WARNING: $($failed.Count) container(s) failed to restart ($($failed -join ', ')); NOT recording marker - will retry next cycle." 'ERROR'
        Write-DeployStatus -Result 'error' -Head $head -PreviousHead $lastDeployed -Restarted $restarted -Detail "failed to restart: $($failed -join ', ')"
        exit 1
    }

    # Record only after a clean restart so a transient failure retries next cycle.
    Set-Content -Path $markerFile -Value $head -NoNewline
    Write-Log "Pipeline now running $shortHead."
    Write-DeployStatus -Result 'deployed' -Head $head -PreviousHead $lastDeployed -Restarted $restarted
} catch {
    # Any unexpected throw (cmdlet error under -ErrorActionPreference Stop, etc.)
    # used to leave the hidden task non-zero with no on-disk trace. Capture it.
    Write-Log "UNEXPECTED ERROR: $($_.Exception.Message)" 'ERROR'
    Write-DeployStatus -Result 'error' -Detail $_.Exception.Message
    exit 1
}
