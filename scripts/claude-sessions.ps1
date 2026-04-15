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
$RepoDir = "C:\Users\mattm\glad-labs-website"
$LogDir = "$env:USERPROFILE\.poindexter\logs\claude-sessions"
$Claude = "$env:USERPROFILE\.local\bin\claude.exe"
$TaskPrefix = "Claude Session"

# Session definitions: name, prompt, schedule, max duration
$Sessions = @{
    "test-health" = @{
        Prompt = "You are in the glad-labs-website repo. Run the full Python test suite: cd src/cofounder_agent && python -m pytest tests/unit/ -q --tb=short -p no:cacheprovider. If any tests fail, analyze whether they are simple bugs (wrong mocks, stale assertions, missing imports). Fix the simple ones. For complex failures, add a comment to the test explaining what is broken. Commit fixes to a new branch auto/test-fixes-{date} and create a Gitea PR. If all tests pass, exit with no changes. Do NOT modify production code, only test files."
        Cron = "0 3 * * *"
        TimeHH = "03"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "test-expansion" = @{
        Prompt = "You are in the glad-labs-website repo. Your job is to expand test coverage. 1) Run: cd src/cofounder_agent && python -m pytest tests/unit/ --co -q to see what is tested. 2) List service files: ls src/cofounder_agent/services/*.py. 3) Find a service file that has NO corresponding test file. 4) Write comprehensive unit tests for it (mock all DB/external calls). 5) Run the new tests to verify they pass. 6) Commit to branch auto/test-expand-{date} and create a Gitea PR. Write at least 10 tests. Focus on edge cases and error paths."
        Cron = "0 4 * * *"
        TimeHH = "04"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "issue-resolver" = @{
        Prompt = "You are in the glad-labs-website repo. Check open Gitea issues for bugs you can fix. Use the forgejo MCP tools to list issues (owner=gladlabs, repo=glad-labs-codebase, state=open). Pick the oldest bug-labeled issue that looks fixable without changing architecture. Read the relevant code, understand the bug, and fix it. Commit to branch auto/fix-issue-{number} and create a Gitea PR referencing the issue. If no issues are simple enough to fix, pick one and add a detailed analysis comment instead. Do NOT close issues, only create PRs. Matt reviews and merges."
        Cron = "0 5 * * *"
        TimeHH = "05"
        TimeMM = "00"
        Days = "daily"
        MaxMinutes = 30
    }
    "codebase-audit" = @{
        Prompt = "You are in the glad-labs-website repo. Run a codebase health audit: 1) Check for unused imports: cd src/cofounder_agent && python -m ruff check --select F401 . 2) Check for security issues: python -m bandit -r services/ routes/ -q. 3) Run pip-audit for dependency vulnerabilities. 4) Check for dead code (functions/classes never imported or called). Fix what is safe to fix (unused imports, simple lint). For security findings, create Gitea issues with details. Commit fixes to auto/audit-{date} and create a Gitea PR."
        Cron = "0 2 * * 3"
        TimeHH = "02"
        TimeMM = "00"
        Days = "WED"
        MaxMinutes = 30
    }
    "doc-sync" = @{
        Prompt = "You are in the glad-labs-website repo. Verify documentation accuracy: 1) Read CLAUDE.md and check that key numbers (test count, service count, etc.) match reality. 2) Read docs/operations/runbook.md and verify commands and URLs are current. 3) Check that documented API endpoints actually exist. 4) Verify documented database tables match the schema. Fix any drift you find. Update counts, URLs, and commands to match current state. Commit to auto/doc-sync-{date} and create a Gitea PR."
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
    $prompt = $session.Prompt -replace '\{date\}', (Get-Date -Format "yyyy-MM-dd") -replace '\{number\}', 'N'

    Write-Host "[$date] Starting Claude session: $Name"
    Write-Host "Log: $logFile"
    Write-Host "Max duration: $($session.MaxMinutes) minutes"

    # Run Claude Code with the prompt, timeout after MaxMinutes
    $timeout = $session.MaxMinutes * 60
    try {
        $proc = Start-Process -FilePath $Claude `
            -ArgumentList "-p", "`"$prompt`"", "--output-format", "text" `
            -WorkingDirectory $RepoDir `
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
    $wrapper = "$RepoDir\scripts\run-claude-session.cmd"

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
