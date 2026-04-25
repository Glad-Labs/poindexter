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
$RepoPreamble = "Your working directory on launch is C:\Users\mattm. Before running ANY shell commands, cd into C:\Users\mattm\glad-labs-website - that is where the repo lives. All relative paths below are relative to the repo root. "

# Session definitions: name, prompt, schedule, max duration
$Sessions = @{
    "test-health" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Run the full Python test suite: cd src/cofounder_agent && python -m pytest tests/unit/ -q --tb=short -p no:cacheprovider. If tests pass, exit with no changes. If tests fail, analyze whether they are simple bugs (wrong mocks, stale assertions, missing imports). Fix the simple ones only. For complex failures, add a # FIXME comment. Use the forgejo MCP tools for Gitea. Create branch auto/test-fixes-{date}, commit, push via git, and create a Gitea PR against main. Do NOT modify production code (only files in tests/). Do NOT push to main directly. Do NOT merge the PR yourself - Matt reviews. Keep output minimal."
        Cron = "0 3 * * *"
        TimeHH = "03"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "test-expansion" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Pick ONE existing service test file with low test count (grep 'def test_' tests/unit/services/*.py | cut -d: -f1 | sort | uniq -c | sort -n | head -5). Read that service's source and the existing tests. Add 5-10 NEW test cases covering edge cases and error paths that aren't already covered. Do NOT duplicate existing tests. Run the new tests to verify they pass. Commit to branch auto/test-expand-{date}, push, create a Gitea PR. Do NOT push to main. Keep output minimal."
        Cron = "0 4 * * *"
        TimeHH = "04"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "issue-resolver" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Use the forgejo MCP tools to list open issues (owner=gladlabs, repo=glad-labs-codebase, state=open, sort=oldest). Pick ONE issue that is clearly scoped, not marked Backlog, and fixable without architectural decisions. Skip anything involving Lemon Squeezy, DNS, secret rotation, or 'Matt decides' clauses. Read the code, understand the bug, make a targeted fix. Commit to branch auto/fix-issue-{number}, push via git, create a Gitea PR referencing the issue. Do NOT close the issue - let Matt merge the PR. Do NOT push to main. If no suitable issue exists, add an analysis comment to ONE issue explaining what you found. Keep output minimal."
        Cron = "0 5 * * *"
        TimeHH = "05"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "codebase-audit" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Run a code quality audit: 1) Unused imports: cd src/cofounder_agent && python -m ruff check --select F401 services/ routes/. 2) Security: python -m bandit -r services/ routes/ -q -ll. 3) Fix only the unused-import issues (safe mechanical fix). For security findings with severity MEDIUM or HIGH, create Gitea issues describing the finding. Commit unused-import fixes to branch auto/audit-{date}, push, create a Gitea PR. Do NOT push to main. Keep output minimal."
        Cron = "0 2 * * 3"
        TimeHH = "02"
        TimeMM = "00"
        Days = "WED"
        MaxMinutes = 30
    }
    "doc-sync" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously for an overnight documentation review pass. Work through this checklist in order, stopping early when out of budget. CHECKLIST: 1) CLAUDE.md numbers - count Python unit tests (cd src/cofounder_agent && python -m pytest tests/unit/ --co -q 2>&1 | tail -1), services (ls src/cofounder_agent/services/*.py | wc -l), grafana dashboards (ls infrastructure/grafana/dashboards/*.json | wc -l), published posts (psql via brain.bootstrap if available, else skip). Fix any number off by >10 percent. 2) CLAUDE.md file paths - verify every file path mentioned actually exists; fix or remove dead refs. 3) docs/ tree - for each *.md file, check that any code paths or commands it references still exist; flag the file (don't rewrite content) by appending a single line at the bottom in HTML comment form noting what's stale. 4) Retired terminology sweep - grep the repo for stale terms: 'Woodpecker' (retired 2026-04-24, replaced by Gitea Actions), '$29 guide' or 'Quick Start Guide' product references (the $29 SKU was killed 2026-04-24, only the $9.99/89.99 Pro tier remains). For each hit in docs/ or README.md, update the wording or open a Gitea issue if the change is non-mechanical. 5) ~/.claude/projects/C--Users-mattm/memory - flag any memory file with a date older than 21 days in a HTML comment at the top. Do NOT rewrite memory content. ONLY commit if the diff is non-empty. Branch auto/doc-sync-{date}, push via git, open a Gitea PR (owner=gladlabs, repo=glad-labs-codebase) titled 'docs: overnight sync {date}' with a summary of what changed. Do NOT push to main directly. Keep output minimal - concise step-by-step log only."
        Cron = "0 6 * * *"
        TimeHH = "06"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
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
