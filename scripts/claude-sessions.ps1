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
$RepoPreamble = "Your working directory on launch is C:\Users\mattm. Before running ANY shell commands, cd into C:\Users\mattm\glad-labs-website - that is where the repo lives. All relative paths below are relative to the repo root. Two GitHub repos: Glad-Labs/glad-labs-stack (private, full tree, push here for daily work) and Glad-Labs/poindexter (public mirror, auto-synced via GitHub Action). Issue routing: public product bugs/features -> Glad-Labs/poindexter; operator/business/Glad-Labs-only -> Glad-Labs/glad-labs-stack. Use the gh CLI for all GitHub operations (gh pr create, gh issue list, etc.). Gitea was decommissioned 2026-04-30 - do NOT use forgejo MCP tools or any localhost:3001 URLs. "

# Session definitions: name, prompt, schedule, max duration
$Sessions = @{
    "test-health" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Run the full Python test suite: cd src/cofounder_agent ; poetry run pytest tests/unit/ -q --tb=short -p no:cacheprovider. (PowerShell 5.1 does not support && - use ; or run as a single bash -c pipeline). If tests pass, exit with no changes. If tests fail, analyze whether they are simple bugs (wrong mocks, stale assertions, missing imports). Fix the simple ones only. For complex failures, add a # FIXME comment. Create branch auto/test-fixes-{date}, commit, git push -u origin, then gh pr create --repo Glad-Labs/glad-labs-stack --base main with a clear title. Do NOT modify production code (only files in tests/). Do NOT push to main directly. Do NOT merge the PR yourself - Matt reviews. Keep output minimal."
        Cron = "0 3 * * *"
        TimeHH = "03"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "test-expansion" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Pick ONE existing service test file with low test count: ls src/cofounder_agent/tests/unit/services/test_*.py | ForEach-Object { [PSCustomObject]@{ File = `$_.Name; Count = (Select-String -Path `$_ -Pattern '^\s*def test_').Count } } | Sort-Object Count | Select-Object -First 5. Read that service's source and the existing tests. Add 5-10 NEW test cases covering edge cases and error paths that are not already covered. Do NOT duplicate existing tests. Run the new tests to verify they pass. Commit to branch auto/test-expand-{date}, push via git push -u origin, create a GitHub PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        Cron = "0 4 * * *"
        TimeHH = "04"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "issue-resolver" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. List open issues in the public product repo: gh issue list --repo Glad-Labs/poindexter --state open --limit 20 --json number,title,labels. Pick ONE issue that is clearly scoped, not labeled 'feature' or 'epic', and fixable without architectural decisions. Skip anything involving Lemon Squeezy, DNS, secret rotation, or 'Matt decides' clauses. Read the code, understand the bug, make a targeted fix. Commit to branch auto/fix-poindexter-{number}, push via git push -u origin, create a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main referencing the issue (title format: 'fix: <description> (closes Glad-Labs/poindexter#NNN)'). Do NOT close the issue - let Matt merge the PR. Do NOT push to main. If no suitable issue exists, add an analysis comment to ONE issue via gh issue comment explaining what you found. Keep output minimal."
        Cron = "0 5 * * *"
        TimeHH = "05"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "codebase-audit" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Run a code quality audit: 1) Unused imports: cd src/cofounder_agent ; poetry run ruff check --select F401 services/ routes/. 2) Security: poetry run bandit -r services/ routes/ -q -ll. 3) Fix only the unused-import issues (safe mechanical fix). For security findings with severity MEDIUM or HIGH, file GitHub issues via gh issue create --repo Glad-Labs/poindexter --label security describing the finding. Commit unused-import fixes to branch auto/audit-{date}, push via git push -u origin, create a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        Cron = "0 2 * * 3"
        TimeHH = "02"
        TimeMM = "00"
        Days = "WED"
        MaxMinutes = 30
    }
    "doc-sync" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Check CLAUDE.md for stale numbers: count tests (cd src/cofounder_agent ; poetry run pytest tests/unit/ --co -q 2>&1 | Select-Object -Last 1), count services (Get-ChildItem src/cofounder_agent/services/*.py | Measure-Object | Select Count), count Grafana dashboards (Get-ChildItem infrastructure/grafana/dashboards/*.json | Measure-Object | Select Count). If any CLAUDE.md number is off by more than 10 percent, update it. Also verify any referenced file paths in CLAUDE.md still exist. Commit fixes to auto/doc-sync-{date}, push via git push -u origin, create a PR via gh pr create --repo Glad-Labs/glad-labs-stack --base main. Do NOT push to main. Keep output minimal."
        Cron = "0 5 * * 5"
        TimeHH = "05"
        TimeMM = "00"
        Days = "FRI"
        MaxMinutes = 20
    }
    "alert-triage" = @{
        Prompt = $RepoPreamble + "Sweep last 24h of alert_events. Connect via DATABASE_URL or LOCAL_DATABASE_URL env. Query: SELECT alertname, severity, COUNT(*) FROM alert_events WHERE received_at > NOW() - INTERVAL '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20. For each alertname firing more than 5 times, look at the most recent dispatch_result + the underlying probe (brain/<name>_probe.py if it exists). If the pattern is clearly a probe bug (false positive, same fingerprint repeating because dedup is broken, etc.), open a SHORT issue at Glad-Labs/poindexter with reproduction + suspect file. If the pattern is a real failure (service genuinely down, GPU overheating, cost overrun) leave alone - the operator will see it on the morning brief. Do NOT dispatch fix agents from inside this session - just file issues. One PR per real bug, branch auto/alert-triage-{date}. Keep output minimal."
        Cron = "0 1 * * *"
        TimeHH = "01"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 25
    }
    "dependency-review" = @{
        Prompt = $RepoPreamble + "Scan for ready-to-merge dependabot/renovate PRs. Run: gh pr list --repo Glad-Labs/glad-labs-stack --search 'is:pr is:open author:app/dependabot status:success' --json number,title,headRefName,createdAt --limit 30. For each PR: only act if (a) the title is a patch-level bump (regex: 'bump .* from \d+\.\d+\.\d+ to \d+\.\d+\.[1-9]\d*' i.e. third number changed, or 'bump .* from \d+\.X\.Y to \d+\.X\.Z' with same major/minor), (b) all checks are green, (c) PR is more than 6 hours old (so any flaky CI has stabilised). For matching PRs run: gh pr review --approve, then gh pr merge --squash --delete-branch --auto. Major-version bumps and minor-version bumps go untouched - those are operator review. Print a summary of merged + skipped + reasons. Keep output minimal."
        Cron = "30 6 * * *"
        TimeHH = "06"
        TimeMM = "30"
        Days = "daily"
        MaxMinutes = 15
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
    $prompt = $RepoPreamble + ($session.Prompt -replace '\{date\}', (Get-Date -Format "yyyy-MM-dd") -replace '\{number\}', 'N')

    Write-Host "[$date] Starting Claude session: $Name"
    Write-Host "Log: $logFile"
    Write-Host "Max duration: $($session.MaxMinutes) minutes"

    # Run Claude Code with the prompt, timeout after MaxMinutes
    # --dangerously-skip-permissions: required for autonomous sessions to
    # run Bash, git, edit files, etc. without blocking on prompts.
    # Safe because sessions are sandboxed to the repo dir and changes go
    # to branches/PRs, never main.
    $timeout = $session.MaxMinutes * 60
    try {
        $proc = Start-Process -FilePath $Claude `
            -ArgumentList "-p", "`"$prompt`"", "--output-format", "text", "--dangerously-skip-permissions" `
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
        Write-Host "  $name - $($s.Days) at $($s.TimeHH):$($s.TimeMM) (max $($s.MaxMinutes)min)"
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