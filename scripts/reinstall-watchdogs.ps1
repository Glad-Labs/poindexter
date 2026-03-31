<#
.SYNOPSIS
    Reinstall watchdogs with ironclad settings — infinite repetition, hidden, auto-restart.
#>

$ErrorActionPreference = "Stop"

# Remove old tasks
Unregister-ScheduledTask -TaskName "OpenClaw Watchdog" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "Claude Code Watchdog" -Confirm:$false -ErrorAction SilentlyContinue

$watchdogs = @(
    @{ Name = "OpenClaw Watchdog"; Script = "C:\Users\mattm\glad-labs-website\scripts\openclaw-watchdog.ps1" },
    @{ Name = "Claude Code Watchdog"; Script = "C:\Users\mattm\glad-labs-website\scripts\claude-code-watchdog.ps1" }
)

foreach ($w in $watchdogs) {
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($w.Script)`""

    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 2) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -Hidden

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

    Register-ScheduledTask -TaskName $w.Name `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $w.Name
    Write-Host "Installed + started: $($w.Name)" -ForegroundColor Green
}

Write-Host ""
Get-ScheduledTask -TaskName "OpenClaw*","Claude Code*" | Format-Table TaskName, State -AutoSize
