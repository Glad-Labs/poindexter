<#
.SYNOPSIS
    One-shot fix: re-registers scheduled tasks that were spawning visible console
    windows. Run ONCE from an elevated (Run as Administrator) PowerShell prompt.

.DESCRIPTION
    Wraps cmd.exe and bash.exe task actions in a hidden powershell.exe call.
    Child processes inherit the parent's hidden console, so no window appears.
    The currently-running instances (Recovery Agent, MCP HTTP) are not killed --
    the fix takes effect on next logon or manual task restart.

.EXAMPLE
    # Elevated terminal:
    powershell -ExecutionPolicy Bypass -File scripts\fix-task-window-visibility.ps1
#>
#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$ps   = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$bash = "C:\Program Files\Git\bin\bash.exe"

# Resolve the .poindexter scripts directory from the current user's profile
# so this script ships without hardcoded operator paths.
$poindexter = "$env:USERPROFILE\.poindexter"
$poindexterBash = ($env:USERPROFILE -replace '\\', '/') + '/.poindexter'

function Wrap-HiddenPS {
    param([string]$TaskName, [string]$Command)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $action = New-ScheduledTaskAction `
        -Execute $ps `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$Command`""
    Set-ScheduledTask -TaskName $TaskName `
        -Action $action `
        -Trigger $task.Triggers `
        -Settings $task.Settings | Out-Null
    Write-Host "Fixed: $TaskName"
}

# Poindexter Recovery Agent — long-running Python HTTP server via cmd script
Wrap-HiddenPS "Poindexter Recovery Agent" "& '$poindexter\scripts\recovery-agent.cmd'"

# Poindexter MCP HTTP — long-running uv/Python HTTP server via cmd script
Wrap-HiddenPS "Poindexter MCP HTTP" "& '$poindexter\scripts\poindexter-mcp-http.cmd'"

# DR-Backup scripts — bash.exe; child inherits hidden PS console so no Bash window
Wrap-HiddenPS "GladLabs-DR-Backup" "& '$bash' -lc '$poindexterBash/scripts/dr-backup/run-backup.sh'"
Wrap-HiddenPS "GladLabs-DR-Backup-Hourly" "& '$bash' -lc '$poindexterBash/scripts/dr-backup/run-hourly-pg.sh'"

Write-Host ""
Write-Host "Done. Recovery Agent and MCP HTTP need a restart to pick up the change:"
Write-Host "  Stop-ScheduledTask 'Poindexter Recovery Agent'; Start-ScheduledTask 'Poindexter Recovery Agent'"
Write-Host "  Stop-ScheduledTask 'Poindexter MCP HTTP';      Start-ScheduledTask 'Poindexter MCP HTTP'"
