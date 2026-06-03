<#
.SYNOPSIS
    Scheduled autonomous Claude Code sessions for continuous improvement.
    Each session gets a focused prompt, works in the repo, and exits.
    All changes go to branches - never commits directly to main.

.PARAMETER Session
    Which session to run: test-health, test-expansion, issue-resolver,
    codebase-audit, doc-sync

.PARAMETER Install
    Register all sessions as Windows Scheduled Tasks.

.PARAMETER Uninstall
    Remove all Claude session scheduled tasks.

.PARAMETER List
    Show all registered Claude session tasks.

.NOTES
    File MUST be saved as UTF-8 with BOM. Without the BOM, PowerShell 5.1
    (powershell.exe) parses non-ASCII chars as ANSI/Windows-1252 garbage,
    breaks string termination, and the whole script fails to run silently
    (Task Scheduler logs exit code 0x1, no log files appear). The current
    `param()` block + the prompts below are intentionally ASCII-only so a
    re-save without BOM still works, but keep BOM as defense in depth.

.EXAMPLE
    .\claude-sessions.ps1 -Session test-health
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
# Launch from $StartDir so all sessions share the C--users-mattm memory bank.
# Prompts below tell Claude to cd into $WorkDir (the repo) for its actual work.
$StartDir = "C:\Users\mattm"
$WorkDir  = "C:\Users\mattm\glad-labs-website"
$LogDir   = "$env:USERPROFILE\.poindexter\logs\claude-sessions"
$Claude   = "$env:USERPROFILE\.local\bin\claude.exe"
$TaskPrefix = "Claude Session"
$WorktreeRoot = "$env:USERPROFILE\.poindexter\worktrees"
$RepoPreamble = "Two GitHub repos: Glad-Labs/glad-labs-stack (private, full tree, SOURCE OF TRUTH for all code) and Glad-Labs/poindexter (public OSS mirror, auto-synced one-way from glad-labs-stack and force-rebuilt on every sync). ROUTING RULE: (1) ISSUES are content-routed to EITHER repo - OSS/product issues belong in Glad-Labs/poindexter, business-ops/internal issues in Glad-Labs/glad-labs-stack; you may create, label, and comment on issues in BOTH (but security-vulnerability issues stay in the private glad-labs-stack - never disclose vulns in the public repo). (2) CODE and PULL REQUESTS go to Glad-Labs/glad-labs-stack ONLY - never push code or open a PR against poindexter; it is force-rebuilt from glad-labs-stack on every sync, so any code change there is destroyed. Always pass --repo explicitly. Gitea was decommissioned 2026-04-30 - do NOT use forgejo MCP tools or any localhost:3001 URLs. "

# Session definitions: name, prompt, schedule, max duration
$Sessions = @{
    "test-health" = @{
        Prompt = "You are running autonomously in a dedicated worktree. Run the unit suite: cd src/cofounder_agent ; poetry run pytest tests/unit/ -q --tb=short -p no:cacheprovider --continue-on-collection-errors (PowerShell 5.1 has no '&&'; use ';'). IGNORE collection errors (E) - they are known host/container path-depth quirks (~21, tracked separately), NOT your job. Act ONLY on real test FAILURES (F). If there are none, exit with no changes. Fix only SIMPLE failures (wrong mocks, stale assertions, missing imports) and ONLY in files under tests/ - never production code. For complex failures add a '# FIXME:' note in the test instead. Commit on the current branch, push via git push -u origin HEAD, then gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main, do NOT merge. Keep output minimal."
        Cron = "0 3 * * *"
        TimeHH = "03"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "test-expansion" = @{
        Prompt = "You are running autonomously in a dedicated worktree. Find the lowest-covered test files across the WHOLE unit tree (not just top-level services): cd src/cofounder_agent ; Get-ChildItem -Recurse tests/unit -Filter 'test_*.py' | ForEach-Object { [PSCustomObject]@{ File = `$_.FullName; Count = (Select-String -Path `$_.FullName -Pattern '^\s*(async )?def test_').Count } } | Sort-Object Count | Select-Object -First 5. Pick ONE, read the service it covers and its existing tests, then add 5-10 NEW cases for uncovered edge/error paths (do NOT duplicate existing tests). Run the new tests to confirm they pass. Commit on the current branch, push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        Cron = "0 4 * * *"
        TimeHH = "04"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "issue-resolver" = @{
        Prompt = "You are running autonomously in a dedicated worktree. List open issues from BOTH repos: gh issue list --repo Glad-Labs/glad-labs-stack --state open --limit 30 --json number,title,labels,url,assignees ; gh issue list --repo Glad-Labs/poindexter --state open --limit 30 --json number,title,labels,url,assignees. Pick ONE clearly-scoped, fixable issue. SKIP anything labeled feature, epic, blocked, or needs-human, and skip Lemon Squeezy / DNS / secret-rotation / 'Matt decides' topics. ALSO SKIP any issue that ALREADY HAS AN ASSIGNEE - another session (interactive or scheduled) has claimed it; never collide. The MOMENT you choose an issue, CLAIM IT before any code work so no other session grabs it: gh issue edit --repo <its repo> <NNN> --add-assignee @me ; gh issue comment --repo <its repo> <NNN> --body 'Claimed by the scheduled issue-resolver - fix incoming.' If the issue shows an assignee by the time you go to claim it, drop it and pick another. Read the code and make a targeted fix. The CODE PR ALWAYS goes to Glad-Labs/glad-labs-stack (the code source of truth; poindexter is force-rebuilt and cannot take code) - commit on the current branch, push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main with title 'fix: <desc> (closes Glad-Labs/<repo>#NNN)' (a cross-repo close reference works when the source issue is in poindexter). Do NOT push to main, do NOT merge. If no suitable issue exists, add ONE analysis comment on the source issue (either repo) and exit. Keep output minimal."
        Cron = "0 5 * * *"
        TimeHH = "05"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
        Model = "claude-opus-4-8"
    }
    "codebase-audit" = @{
        Prompt = "You are running autonomously in a dedicated worktree. Run: cd src/cofounder_agent ; poetry run ruff check --select F401 services/ routes/ ; poetry run bandit -r services/ routes/ -q -ll. Fix ONLY the unused-import (F401) findings - the safe mechanical fix. For bandit MEDIUM/HIGH findings, file a GitHub issue via gh issue create --repo Glad-Labs/glad-labs-stack --label security (security findings stay in the PRIVATE repo - never disclose vulns in the public poindexter). Commit the import fixes on the current branch (code PRs go to glad-labs-stack), push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        Cron = "0 2 * * 3"
        TimeHH = "02"
        TimeMM = "00"
        Days = "WED"
        MaxMinutes = 30
    }
    "doc-sync" = @{
        Prompt = "You are running autonomously in a dedicated worktree. Verify that file paths REFERENCED in CLAUDE.md still exist - do NOT recompute stat counts (the sync-claude-md.yml GitHub Action owns counts; recomputing them here double-counts subdirectories and corrupts the doc). Extract path-like references from CLAUDE.md (src/..., docs/..., infrastructure/..., scripts/..., brain/...) and check each exists on disk. For any reference that no longer resolves, fix it to the correct current path or remove the stale line. If every reference resolves, exit with no changes. Commit fixes on the current branch, push via git push -u origin HEAD, open a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT touch numeric stat counts. Keep output minimal."
        Cron = "0 5 * * 5"
        TimeHH = "05"
        TimeMM = "00"
        Days = "FRI"
        MaxMinutes = 20
    }
    "alert-triage" = @{
        Prompt = "Sweep the last 24h of alert_events. Connect via the DATABASE_URL or LOCAL_DATABASE_URL env. Query: SELECT alertname, severity, COUNT(*) FROM alert_events WHERE received_at > NOW() - INTERVAL '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20. For each alertname firing more than 5 times, inspect the most recent dispatch_result and the underlying probe (brain/<name>_probe.py if it exists). If it is clearly a probe bug (false positive, or broken dedup repeating one fingerprint), open a SHORT issue at Glad-Labs/glad-labs-stack with reproduction + suspect file via gh issue create --repo Glad-Labs/glad-labs-stack. If it is a real failure (service down, GPU overheating, cost overrun), leave it - the operator sees it on the morning brief. Do NOT dispatch fix agents; just file issues. One issue per real probe bug. Keep output minimal."
        Cron = "0 1 * * *"
        TimeHH = "01"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 25
    }
    "dependency-review" = @{
        Prompt = "Scan for ready-to-merge dependabot PRs: gh pr list --repo Glad-Labs/glad-labs-stack --search 'is:pr is:open author:app/dependabot status:success' --json number,title,headRefName,createdAt --limit 30. Act ONLY when (a) the title is a PATCH bump (third version number changed, same major.minor), (b) all checks are green, and (c) the PR is older than 6 hours (so flaky CI has settled). For matches: gh pr review --approve, then gh pr merge --squash --delete-branch --auto. Leave minor and major bumps for operator review. Print a summary of merged + skipped + reasons. Keep output minimal."
        Cron = "30 6 * * *"
        TimeHH = "06"
        TimeMM = "30"
        Days = "daily"
        MaxMinutes = 15
    }
    "triage-sweep" = @{
        Prompt = "This is a read-and-triage session that makes NO code changes and opens NO pull request - ignore any instruction above to create a branch, commit, or open a PR. Your only writes are GitHub issue-label edits (in EITHER repo) plus one Discord message. Steps: (1) From the worktree run: cd src/cofounder_agent ; poetry run python ../../scripts/triage/run_weekly_sweep.py  (PowerShell 5.1 has no '&&'; use ';'). It applies content-derived 'type' labels in BOTH repos and prints a JSON report: per-repo gaps + each repo's milestones. (2) For each gap missing 'area': apply the single best area label via gh issue edit --repo <that gap's repo> --add-label <area> ONLY when the body clearly cites one subsystem (backend, frontend, testing, infra, monitoring, pipeline, monetization); if it is cross-cutting, leave it bare. (3) Never apply priority or milestone - compose a one-line proposal per gap instead (priority from blocking/impact signals in the body; milestone from that repo's milestones list). (4) Post ONE Discord digest via the gladlabs discord_post MCP tool, title 'Weekly triage: N proposals', listing each issue, its repo, the proposed labels, and a one-line rationale. Cite-or-surface: never invent a value you cannot cite from the issue body; a bare axis is correct when there is no basis. Keep output minimal."
        Cron = "0 7 * * 1"
        TimeHH = "07"
        TimeMM = "00"
        Days = "MON"
        MaxMinutes = 30
    }
    "claude-md-sync" = @{
        Prompt = "You are running autonomously in a dedicated worktree. GOAL: keep CLAUDE.md's DB-derived counts and migration narrative in sync with prod (the deterministic numbers + the LLM-judgment prose that the CI sync-claude-md.yml Action does NOT handle). STEP 1 (deterministic numbers): run the DB-count sync that lives in YOUR worktree, but through the MAIN checkout's poetry env - a fresh worktree venv lacks the asyncpg driver, and the script edits YOUR worktree's CLAUDE.md in place via its own __file__ path regardless of which env runs it. Run: cd C:\Users\mattm\glad-labs-website\src\cofounder_agent ; poetry run python YOUR_WORKTREE_PATH\scripts\sync_claude_md_db_stats.py  (substitute YOUR_WORKTREE_PATH with the worktree dir from the preamble above; PowerShell 5.1 has no '&&', use ';'). It prints which counts changed (live/total posts, pipeline_tasks, app_settings, embeddings). Do NOT recompute repo file-stat counts (service .py / test files / dashboards) - sync-claude-md.yml owns those; touching them here double-counts. STEP 2 (narrative, LLM judgment): cd back into your worktree, then compare the 'Latest as of YYYY-MM-DD:' migration line in CLAUDE.md against the newest timestamped file(s) under src/cofounder_agent/services/migrations/ - if a newer migration has landed, update that line (filename + a one-clause description of what it does, read from the file). Only change what you can verify from the repo; do NOT speculatively rewrite architecture prose. STEP 3: if neither step changed CLAUDE.md, exit with no commit. Otherwise commit on the current branch with 'git commit --no-verify' (REQUIRED - the pre-commit prettier hook mangles CLAUDE.md prose containing '*' glob tokens and the eslint hook is broken repo-wide), push via git push -u origin HEAD, then gh pr create --repo Glad-Labs/glad-labs-stack --base main --title 'docs(CLAUDE.md): sync DB-derived counts + migration narrative (auto)'. Do NOT push to main, do NOT merge. Keep output minimal."
        Cron = "30 2 * * *"
        TimeHH = "02"
        TimeMM = "30"
        Days = "daily"
        MaxMinutes = 20
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

    # Create log directory
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

    $date = Get-Date -Format "yyyy-MM-dd-HHmm"
    $logFile = "$LogDir\$Name-$date.log"

    # --- Worktree isolation -------------------------------------------------
    # Each session works in its OWN git worktree on a pre-created branch off
    # the LATEST origin/main - never in the shared C:\Users\mattm\glad-labs-website
    # checkout. Two reasons:
    #   1. Concurrent sessions (and Matt's interactive session) all share that
    #      one checkout; a `git checkout -b` in one swaps the branch out from
    #      under another mid-edit -> commits land on the wrong branch / PRs
    #      get cross-contaminated (observed 2026-05-30). Worktrees give each
    #      session an isolated HEAD.
    #   2. Branching off freshly-fetched origin/main means a session never
    #      inherits another session's unmerged commits as its base.
    # The Claude process still LAUNCHES from $StartDir so it keeps the shared
    # C--Users-mattm memory bank (the project-memory key is tied to the launch
    # dir, not the cwd); the prompt cd's into the worktree for all real work.
    if (-not (Test-Path $WorktreeRoot)) { New-Item -ItemType Directory -Path $WorktreeRoot -Force | Out-Null }
    $branch = "auto/$Name-$date"
    $wt = "$WorktreeRoot\$Name-$date"

    # Clear out any stale worktree registrations from prior crashed runs.
    git -C $WorkDir worktree prune 2>&1 | Out-Null
    git -C $WorkDir fetch origin --quiet 2>&1 | Out-Null
    git -C $WorkDir worktree add -b $branch $wt origin/main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $wt)) {
        Add-Content $logFile "`n[ERROR] Failed to create worktree $wt on branch $branch; aborting session (refusing to run in the shared checkout)."
        Write-Host "FAILED to create worktree for $Name; aborting."
        return
    }

    # The husky/lint-staged pre-commit hook (npx lint-staged -> prettier/eslint)
    # needs node_modules, which a fresh worktree does NOT have (node_modules is
    # gitignored, not part of the checkout). Without it the hook fails and the
    # session can't commit (e.g. any commit that stages a .md/.json/.js file).
    # Junction the shared checkout's node_modules into the worktree so the hook
    # resolves its tools. A junction (not a copy) is instant and read-shared.
    # Torn down in finally BEFORE the worktree is removed (see below).
    cmd /c mklink /J "$wt\node_modules" "$WorkDir\node_modules" 2>&1 | Out-Null

    $wtPreamble = "Your working directory on launch is $StartDir, but a DEDICATED git worktree has been created for this session at $wt, already checked out to branch '$branch' (based on the latest origin/main). Before running ANY shell commands, cd into $wt - that is your ISOLATED checkout and all relative paths below are relative to it. Do ALL git and file work there. You are ALREADY on branch '$branch': do NOT run git checkout / git switch / git worktree, do NOT create another branch, and do NOT touch the shared checkout at C:\Users\mattm\glad-labs-website (another session may be using it). When you have changes, commit on '$branch' and push with: git push -u origin $branch ; then open a PR with gh pr create --repo Glad-Labs/glad-labs-stack --base main. Ignore any instruction below to 'create a branch auto/...' - your branch already exists; just use it. If you make no changes, exit without committing. "
    $prompt = $wtPreamble + $RepoPreamble + ($session.Prompt -replace '\{date\}', (Get-Date -Format "yyyy-MM-dd") -replace '\{number\}', 'N')

    Write-Host "[$date] Starting Claude session: $Name"
    Write-Host "Worktree: $wt (branch $branch)"
    Write-Host "Log: $logFile"
    Write-Host "Max duration: $($session.MaxMinutes) minutes"

    # Run Claude Code with the prompt, timeout after MaxMinutes
    # --dangerously-skip-permissions: required for autonomous sessions to
    # run Bash, git, edit files, etc. without blocking on prompts.
    # Safe because sessions are sandboxed to their worktree and changes go
    # to branches/PRs, never main.
    $timeout = $session.MaxMinutes * 60
    # Per-session model (default sonnet-4-6 for routine hygiene; opus for the
    # judgment-heavy bug-fixer). Unset Model falls back to the sonnet default.
    $model = if ($session.Model) { $session.Model } else { "claude-sonnet-4-6" }
    try {
        $proc = Start-Process -FilePath $Claude `
            -ArgumentList "-p", "`"$prompt`"", "--model", $model, "--output-format", "text", "--dangerously-skip-permissions" `
            -WorkingDirectory $StartDir `
            -RedirectStandardOutput $logFile `
            -RedirectStandardError "$logFile.err" `
            -NoNewWindow -PassThru

        if (-not $proc.WaitForExit($timeout * 1000)) {
            $proc.Kill()
            Add-Content $logFile "`n[TIMEOUT] Session killed after $($session.MaxMinutes) minutes"
        }
    } catch {
        Add-Content $logFile "`n[ERROR] $($_.Exception.Message)"
    } finally {
        # Remove the node_modules JUNCTION first, with rmdir — it deletes the
        # link only and NEVER follows the junction into the shared checkout's
        # real node_modules. Doing this before `worktree remove` is critical:
        # a recursive delete that traversed the junction would wipe the shared
        # node_modules.
        if (Test-Path "$wt\node_modules") { cmd /c rmdir "$wt\node_modules" 2>&1 | Out-Null }
        # Always tear down the worktree, even on timeout/kill, so it never
        # accumulates stale trees or leaves a half-checked-out branch around.
        # The session's branch + PR live on the remote once pushed; the local
        # branch is disposable (force-delete; unpushed = no-change sessions).
        git -C $WorkDir worktree remove $wt --force 2>&1 | Out-Null
        git -C $WorkDir branch -D $branch 2>&1 | Out-Null
        git -C $WorkDir worktree prune 2>&1 | Out-Null
    }

    Write-Host "[$((Get-Date).ToString('HH:mm:ss'))] Session complete: $Name"
}

function Install-Sessions {
    $wrapper = "$WorkDir\scripts\run-claude-session.cmd"

    foreach ($name in $Sessions.Keys) {
        $s = $Sessions[$name]
        $taskName = "$TaskPrefix - $name"

        # Remove existing if present
        $null = schtasks /Delete /TN $taskName /F 2>&1

        $tr = "`"$wrapper`" $name"
        $st = "$($s.TimeHH):$($s.TimeMM)"

        if ($s.Days -eq "daily") {
            schtasks /Create /TN "$taskName" /TR $tr /SC DAILY /ST $st /F 2>&1 | Out-Null
        } else {
            schtasks /Create /TN "$taskName" /TR $tr /SC WEEKLY /D $($s.Days) /ST $st /F 2>&1 | Out-Null
        }

        # Verify it was created
        $check = schtasks /Query /TN "$taskName" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Registered: $taskName ($($s.Days) at $st)"
        } else {
            Write-Host "FAILED to register: $taskName"
        }
    }

    Write-Host ""
    Write-Host "All sessions installed. Logs: $LogDir"
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
    Write-Host "Registered Claude sessions:`n"
    schtasks /Query /FO TABLE | Select-String "Claude Session"
    Write-Host "`nSession definitions:`n"
    foreach ($name in ($Sessions.Keys | Sort-Object)) {
        $s = $Sessions[$name]
        $m = if ($s.Model) { $s.Model } else { "claude-sonnet-4-6" }
        Write-Host "  $name - $($s.Days) at $($s.TimeHH):$($s.TimeMM) (max $($s.MaxMinutes)min, $m)"
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
    Write-Host "  .\claude-sessions.ps1 -Install           Register all as scheduled tasks"
    Write-Host "  .\claude-sessions.ps1 -Uninstall         Remove all scheduled tasks"
    Write-Host "  .\claude-sessions.ps1 -List              Show registered sessions"
    $names = $Sessions.Keys -join ", "
    Write-Host "`nSessions: $names"
}