<#
.SYNOPSIS
    Scheduled autonomous Claude Code sessions for continuous improvement.
    Each session gets a focused prompt, works in the repo, and exits.
    All changes go to branches — never commits directly to main.

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
$RepoPreamble = "Your working directory on launch is C:\Users\mattm. Before running ANY shell commands, cd into C:\Users\mattm\glad-labs-website — that is where the repo lives. All relative paths below are relative to the repo root. "

# Session definitions: name, prompt, schedule, max duration
$Sessions = @{
    "test-health" = @{
        Prompt = "You are in the glad-labs-website repo running autonomously. Run the full Python test suite: cd src/cofounder_agent && python -m pytest tests/unit/ -q --tb=short -p no:cacheprovider. If tests pass, exit with no changes. If tests fail, analyze whether they are simple bugs (wrong mocks, stale assertions, missing imports). Fix the simple ones only. For complex failures, add a # FIXME comment. Use the forgejo MCP tools for Gitea. Create branch auto/test-fixes-{date}, commit, push via git, and create a Gitea PR against main. Do NOT modify production code (only files in tests/). Do NOT push to main directly. Do NOT merge the PR yourself — Matt reviews. Keep output minimal."
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
        Prompt = "You are in the glad-labs-website repo running autonomously. Use the forgejo MCP tools to list open issues (owner=gladlabs, repo=glad-labs-codebase, state=open, sort=oldest). Pick ONE issue that is clearly scoped, not marked Backlog, and fixable without architectural decisions. Skip anything involving Lemon Squeezy, DNS, secret rotation, or 'Matt decides' clauses. Read the code, understand the bug, make a targeted fix. Commit to branch auto/fix-issue-{number}, push via git, create a Gitea PR referencing the issue. Do NOT close the issue — let Matt merge the PR. Do NOT push to main. If no suitable issue exists, add an analysis comment to ONE issue explaining what you found. Keep output minimal."
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
        Prompt = "You are in the glad-labs-website repo running autonomously. Check CLAUDE.md for stale numbers: count tests (python -m pytest tests/unit/ --co -q 2>&1 | tail -1), count services (ls src/cofounder_agent/services/*.py | wc -l), count Grafana dashboards (ls infrastructure/grafana/dashboards/*.json | wc -l). If any CLAUDE.md number is off by >10%, update it. Also verify any referenced file paths in CLAUDE.md still exist. Commit fixes to auto/doc-sync-{date}, push, create a Gitea PR. Do NOT push to main. Keep output minimal."
        Cron = "0 5 * * 5"
        TimeHH = "05"
        TimeMM = "00"
        Days = "FRI"
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
