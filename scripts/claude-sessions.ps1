<#
.SYNOPSIS
    Scheduled autonomous ops sessions for continuous improvement.

    As of the 2026-07-09 rewire, most sessions run as DETERMINISTIC or
    LOCAL-LLM Python scripts under scripts/ops_sessions/ (no Claude Code,
    no metered API, no OAuth to expire). Two judgment-heavy sessions
    (issue-resolver, test-expansion) still need a frontier cloud model and
    are kept here DISABLED (Enabled = $false) pending a metered-API decision.
    See docs/superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md.

    Each session still runs in an isolated git worktree (when it commits) and
    opens PRs against Glad-Labs/glad-labs-stack - never commits to main.

.PARAMETER Session
    Which session to run (e.g. dependency-review, codebase-audit, doc-sync,
    claude-md-sync, triage-sweep, alert-triage, test-health).

.PARAMETER Install
    Register all ENABLED sessions as Windows Scheduled Tasks.

.PARAMETER Uninstall
    Remove all Claude session scheduled tasks.

.PARAMETER List
    Show all registered session tasks + definitions.

.NOTES
    File MUST be saved as UTF-8 with BOM. Without the BOM, PowerShell 5.1
    (powershell.exe) parses non-ASCII chars as ANSI/Windows-1252 garbage,
    breaks string termination, and the whole script fails to run silently
    (Task Scheduler logs exit code 0x1, no log files appear). The current
    param() block + the definitions below are intentionally ASCII-only so a
    re-save without BOM still works, but keep BOM as defense in depth.

.EXAMPLE
    .\claude-sessions.ps1 -Session dependency-review
    .\claude-sessions.ps1 -Install
    .\claude-sessions.ps1 -List
#>
param(
    [string]$Session,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$List
)

$ErrorActionPreference = "Continue"
# Launch from $StartDir so any legacy claude.exe session shares the
# C--users-mattm memory bank. Ops scripts self-locate their repo root.
$StartDir = "C:\Users\mattm"
$WorkDir  = "C:\Users\mattm\glad-labs-website"
$LogDir   = "$env:USERPROFILE\.poindexter\logs\claude-sessions"
$Claude   = "$env:USERPROFILE\.local\bin\claude.exe"
$TaskPrefix = "Claude Session"
$WorktreeRoot = "$env:USERPROFILE\.poindexter\worktrees"
$RepoPreamble = "Two GitHub repos: Glad-Labs/glad-labs-stack (private, full tree, SOURCE OF TRUTH for all code) and Glad-Labs/poindexter (public OSS mirror, auto-synced one-way from glad-labs-stack and force-rebuilt on every sync). ROUTING RULE: (1) ISSUES are content-routed to EITHER repo - OSS/product issues belong in Glad-Labs/poindexter, business-ops/internal issues in Glad-Labs/glad-labs-stack; you may create, label, and comment on issues in BOTH (but security-vulnerability issues stay in the private glad-labs-stack - never disclose vulns in the public repo). (2) CODE and PULL REQUESTS go to Glad-Labs/glad-labs-stack ONLY - never push code or open a PR against poindexter; it is force-rebuilt from glad-labs-stack on every sync, so any code change there is destroyed. Always pass --repo explicitly. Gitea was decommissioned 2026-04-30 - do NOT use forgejo MCP tools or any localhost:3001 URLs. "

# Session definitions.
#   Command       - the payload to run inside the checkout. Uses the literal
#                   tokens {mainPkg} (the main checkout's src/cofounder_agent,
#                   which owns the provisioned poetry env) and {runDir} (the
#                   worktree when NeedsWorktree, else the shared checkout). Run
#                   via the main env so tooling resolves; the script self-locates
#                   its repo root from {runDir} and reuses sys.executable inside.
#   NeedsWorktree - $true for sessions that commit + open PRs (isolated HEAD);
#                   $false for read/act-via-API sessions (merge PRs / file issues
#                   / edit labels) that never mutate the local checkout.
#   Enabled       - $false keeps the definition but skips registration.
# Legacy sessions carry a Prompt (claude.exe) instead of a Command; they are
# frontier-model-dependent and currently Enabled = $false.
$Sessions = @{
    "dependency-review" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\dependency_review.py"'
        NeedsWorktree = $false
        Enabled = $true
        TimeHH = "06"; TimeMM = "30"; Days = "daily"; MaxMinutes = 15
    }
    "codebase-audit" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\codebase_audit.py"'
        NeedsWorktree = $true
        Enabled = $true
        TimeHH = "02"; TimeMM = "00"; Days = "WED"; MaxMinutes = 30
    }
    "doc-sync" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\doc_sync.py"'
        NeedsWorktree = $true
        Enabled = $true
        TimeHH = "05"; TimeMM = "00"; Days = "FRI"; MaxMinutes = 20
    }
    "claude-md-sync" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\claude_md_sync.py"'
        NeedsWorktree = $true
        Enabled = $true
        TimeHH = "02"; TimeMM = "30"; Days = "daily"; MaxMinutes = 20
    }
    "triage-sweep" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\triage_sweep.py"'
        NeedsWorktree = $false
        Enabled = $true
        TimeHH = "07"; TimeMM = "00"; Days = "MON"; MaxMinutes = 30
    }
    "alert-triage" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\alert_triage.py"'
        NeedsWorktree = $false
        Enabled = $true
        TimeHH = "01"; TimeMM = "00"; Days = "daily"; MaxMinutes = 25
    }
    "test-health" = @{
        Command = 'cd "{mainPkg}"; poetry run python "{runDir}\scripts\ops_sessions\test_health.py"'
        NeedsWorktree = $true
        Enabled = $true
        TimeHH = "03"; TimeMM = "00"; Days = "daily"; MaxMinutes = 30
    }
    # --- Frontier-model sessions: DISABLED pending a metered-API decision. ---
    # They still need a cloud model (a weak local fix is review noise), so they
    # are not rewired. Kept here with their prompts for the future decision.
    "issue-resolver" = @{
        Prompt = "You are running autonomously in a dedicated worktree. List open issues from BOTH repos: gh issue list --repo Glad-Labs/glad-labs-stack --state open --limit 30 --json number,title,labels,url,assignees ; gh issue list --repo Glad-Labs/poindexter --state open --limit 30 --json number,title,labels,url,assignees. Pick ONE clearly-scoped, fixable issue. SKIP anything labeled feature, epic, blocked, or needs-human, and skip Lemon Squeezy / DNS / secret-rotation / 'Matt decides' topics. ALSO SKIP any issue that ALREADY HAS AN ASSIGNEE - another session (interactive or scheduled) has claimed it; never collide. The MOMENT you choose an issue, CLAIM IT before any code work so no other session grabs it: gh issue edit --repo <its repo> <NNN> --add-assignee @me ; gh issue comment --repo <its repo> <NNN> --body 'Claimed by the scheduled issue-resolver - fix incoming.' If the issue shows an assignee by the time you go to claim it, drop it and pick another. Read the code and make a targeted fix. The CODE PR ALWAYS goes to Glad-Labs/glad-labs-stack (the code source of truth; poindexter is force-rebuilt and cannot take code) - commit on the current branch, push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main with title 'fix: <desc> (closes Glad-Labs/<repo>#NNN)' (a cross-repo close reference works when the source issue is in poindexter). Do NOT push to main, do NOT merge. If no suitable issue exists, add ONE analysis comment on the source issue (either repo) and exit. Keep output minimal."
        NeedsWorktree = $true
        Enabled = $false
        TimeHH = "05"; TimeMM = "00"; Days = "daily"; MaxMinutes = 30
    }
    "test-expansion" = @{
        Prompt = "You are running autonomously in a dedicated worktree. Find the lowest-covered test files across the WHOLE unit tree (not just top-level services): cd src/cofounder_agent ; Get-ChildItem -Recurse tests/unit -Filter 'test_*.py' | ForEach-Object { [PSCustomObject]@{ File = `$_.FullName; Count = (Select-String -Path `$_.FullName -Pattern '^\s*(async )?def test_').Count } } | Sort-Object Count | Select-Object -First 5. Pick ONE, read the service it covers and its existing tests, then add 5-10 NEW cases for uncovered edge/error paths (do NOT duplicate existing tests). Run the new tests to confirm they pass. Commit on the current branch, push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        NeedsWorktree = $true
        Enabled = $false
        TimeHH = "04"; TimeMM = "00"; Days = "daily"; MaxMinutes = 30
    }
}

function Run-Session {
    param([string]$Name)

    $session = $Sessions[$Name]
    if (-not $session) {
        Write-Host "Unknown session: $Name"
        Write-Host "Available: $($Sessions.Keys -join ', ')"
        return
    }

    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

    $date = Get-Date -Format "yyyy-MM-dd-HHmm"
    $logFile = "$LogDir\$Name-$date.log"
    $mainPkg = Join-Path $WorkDir "src\cofounder_agent"

    # Sessions that commit run in an isolated worktree; read/act-via-API sessions
    # run from the shared checkout. NeedsWorktree defaults $true when unset (the
    # legacy claude.exe sessions always want isolation).
    $needsWt = $session.NeedsWorktree -ne $false
    $runDir = $WorkDir
    $branch = $null
    $wt = $null

    if ($needsWt) {
        # --- Worktree isolation ---------------------------------------------
        # Each committing session works in its OWN git worktree on a pre-created
        # branch off the LATEST origin/main - never in the shared checkout. A
        # `git checkout -b` in the shared checkout would swap the branch out from
        # under a concurrent session mid-edit (observed 2026-05-30). Branching off
        # freshly-fetched origin/main also means a session never inherits another
        # session's unmerged commits as its base.
        if (-not (Test-Path $WorktreeRoot)) { New-Item -ItemType Directory -Path $WorktreeRoot -Force | Out-Null }
        $branch = "auto/$Name-$date"
        $wt = "$WorktreeRoot\$Name-$date"

        git -C $WorkDir worktree prune 2>&1 | Out-Null
        git -C $WorkDir fetch origin --quiet 2>&1 | Out-Null
        git -C $WorkDir worktree add -b $branch $wt origin/main 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $wt)) {
            Add-Content $logFile "`n[ERROR] Failed to create worktree $wt on branch $branch; aborting session (refusing to run in the shared checkout)."
            Write-Host "FAILED to create worktree for $Name; aborting."
            return
        }

        # The husky/lint-staged pre-commit hook (npx lint-staged -> prettier/eslint)
        # needs node_modules, which a fresh worktree does NOT have. Junction the
        # shared checkout's node_modules into the worktree so the hook resolves its
        # tools. A junction (not a copy) is instant and read-shared. Torn down in
        # finally BEFORE the worktree is removed.
        cmd /c mklink /J "$wt\node_modules" "$WorkDir\node_modules" 2>&1 | Out-Null
        $runDir = $wt
    }

    # Legacy claude.exe sessions build a natural-language prompt; Command sessions
    # do not. $wtPreamble references $wt, so build only when there is no Command
    # (legacy sessions always set NeedsWorktree true, so $wt exists here).
    $prompt = $null
    if (-not $session.Command) {
        $wtPreamble = "Your working directory on launch is $StartDir, but a DEDICATED git worktree has been created for this session at $wt, already checked out to branch '$branch' (based on the latest origin/main). Before running ANY shell commands, cd into $wt - that is your ISOLATED checkout and all relative paths below are relative to it. Do ALL git and file work there. You are ALREADY on branch '$branch': do NOT run git checkout / git switch / git worktree, do NOT create another branch, and do NOT touch the shared checkout at C:\Users\mattm\glad-labs-website (another session may be using it). When you have changes, commit on '$branch' and push with: git push -u origin $branch ; then open a PR with gh pr create --repo Glad-Labs/glad-labs-stack --base main. Ignore any instruction below to 'create a branch auto/...' - your branch already exists; just use it. If you make no changes, exit without committing. "
        $prompt = $wtPreamble + $RepoPreamble + ($session.Prompt -replace '\{date\}', (Get-Date -Format "yyyy-MM-dd") -replace '\{number\}', 'N')
    }

    Write-Host "[$date] Starting session: $Name (runDir $runDir)"
    Write-Host "Log: $logFile"
    Write-Host "Max duration: $($session.MaxMinutes) minutes"

    $timeout = $session.MaxMinutes * 60
    try {
        if ($session.Command) {
            # Deterministic / local-LLM ops script. Run under the main checkout's
            # poetry env (owns the provisioned venv); the script self-locates its
            # repo root from {runDir} and reuses sys.executable for its own tools.
            $cmd = $session.Command.Replace('{mainPkg}', $mainPkg).Replace('{runDir}', $runDir)
            $proc = Start-Process -FilePath "powershell.exe" `
                -ArgumentList @("-NoProfile", "-NonInteractive", "-Command", $cmd) `
                -WorkingDirectory $runDir `
                -RedirectStandardOutput $logFile `
                -RedirectStandardError "$logFile.err" `
                -NoNewWindow -PassThru
        } else {
            # Legacy claude.exe path (frontier sessions; currently disabled).
            $model = if ($session.Model) { $session.Model } else { "claude-sonnet-4-6" }
            $proc = Start-Process -FilePath $Claude `
                -ArgumentList "-p", "`"$prompt`"", "--model", $model, "--output-format", "text", "--dangerously-skip-permissions" `
                -WorkingDirectory $StartDir `
                -RedirectStandardOutput $logFile `
                -RedirectStandardError "$logFile.err" `
                -NoNewWindow -PassThru
        }

        if (-not $proc.WaitForExit($timeout * 1000)) {
            $proc.Kill()
            Add-Content $logFile "`n[TIMEOUT] Session killed after $($session.MaxMinutes) minutes"
        }
    } catch {
        Add-Content $logFile "`n[ERROR] $($_.Exception.Message)"
    } finally {
        if ($needsWt) {
            # Remove the node_modules JUNCTION first, with rmdir - it deletes the
            # link only and NEVER follows the junction into the shared checkout's
            # real node_modules. Doing this before `worktree remove` is critical:
            # a recursive delete that traversed the junction would wipe the shared
            # node_modules.
            if (Test-Path "$wt\node_modules") { cmd /c rmdir "$wt\node_modules" 2>&1 | Out-Null }
            # Always tear down the worktree, even on timeout/kill. The session's
            # branch + PR live on the remote once pushed; the local branch is
            # disposable (force-delete; unpushed = no-change sessions).
            git -C $WorkDir worktree remove $wt --force 2>&1 | Out-Null
            git -C $WorkDir branch -D $branch 2>&1 | Out-Null
            git -C $WorkDir worktree prune 2>&1 | Out-Null
        }
    }

    Write-Host "[$((Get-Date).ToString('HH:mm:ss'))] Session complete: $Name"
}

function Install-Sessions {
    # Windows PowerShell 5.1 directly - stable path across updates.
    $pwshExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $scriptFile = $PSCommandPath

    $dayMap = @{
        "MON" = "Monday"; "TUE" = "Tuesday"; "WED" = "Wednesday"
        "THU" = "Thursday"; "FRI" = "Friday"; "SAT" = "Saturday"; "SUN" = "Sunday"
    }

    foreach ($name in $Sessions.Keys) {
        $s = $Sessions[$name]
        if ($s.Enabled -eq $false) { Write-Host "Skipping disabled: $name"; continue }
        $taskName = "$TaskPrefix - $name"

        # Direct powershell.exe invocation with -WindowStyle Hidden - no cmd wrapper.
        # Child processes inherit the hidden console and never spawn visible windows.
        $action = New-ScheduledTaskAction `
            -Execute $pwshExe `
            -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptFile`" -Session $name"

        $timeStr = "$($s.TimeHH):$($s.TimeMM)"
        $trigger = if ($s.Days -eq "daily") {
            New-ScheduledTaskTrigger -Daily -At $timeStr
        } else {
            New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dayMap[$s.Days] -At $timeStr
        }

        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes $s.MaxMinutes) `
            -MultipleInstances IgnoreNew

        $null = Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Write-Host "Registered: $taskName ($($s.Days) at $timeStr)"
        } else {
            Write-Host "FAILED to register: $taskName"
        }
    }

    Write-Host ""
    Write-Host "All enabled sessions installed. Logs: $LogDir"
}

function Uninstall-Sessions {
    foreach ($name in $Sessions.Keys) {
        $taskName = "$TaskPrefix - $name"
        schtasks /Delete /TN $taskName /F 2>$null | Out-Null
        Write-Host "Removed: $taskName"
    }
    Write-Host "All Claude sessions uninstalled."
}

function List-Sessions {
    Write-Host "Registered session tasks:`n"
    schtasks /Query /FO TABLE | Select-String "Claude Session"
    Write-Host "`nSession definitions:`n"
    foreach ($name in ($Sessions.Keys | Sort-Object)) {
        $s = $Sessions[$name]
        $en = if ($s.Enabled -eq $false) { "DISABLED" } else { "enabled" }
        $mode = if ($s.Command) { "script" } else { "claude.exe" }
        Write-Host "  $name [$en/$mode] - $($s.Days) at $($s.TimeHH):$($s.TimeMM) (max $($s.MaxMinutes)min)"
    }
}

# Main dispatch
if ($Install) { Install-Sessions }
elseif ($Uninstall) { Uninstall-Sessions }
elseif ($List) { List-Sessions }
elseif ($Session) { Run-Session -Name $Session }
else {
    Write-Host "Usage:"
    Write-Host "  .\claude-sessions.ps1 -Session NAME      Run a session"
    Write-Host "  .\claude-sessions.ps1 -Install           Register all enabled as scheduled tasks"
    Write-Host "  .\claude-sessions.ps1 -Uninstall         Remove all scheduled tasks"
    Write-Host "  .\claude-sessions.ps1 -List              Show registered sessions"
    $names = $Sessions.Keys -join ", "
    Write-Host "`nSessions: $names"
}
