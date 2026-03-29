<#
.SYNOPSIS
    Self-healing watchdog for Claude Code + Telegram plugin.

.DESCRIPTION
    Checks:
      1. Claude Code process is running
      2. Telegram bot is reachable (API getMe check)

    Recovery:
      - If Claude Code not running, launches a new session in the glad-labs-website
        project directory (Telegram plugin auto-starts with Claude Code)
      - If bot token is invalid/unreachable, logs a warning

    Modes:
      - One-shot:  .\claude-code-watchdog.ps1
      - Loop:      .\claude-code-watchdog.ps1 -Loop -IntervalSeconds 120
      - Install:   .\claude-code-watchdog.ps1 -Install
      - Uninstall: .\claude-code-watchdog.ps1 -Uninstall

.EXAMPLE
    .\claude-code-watchdog.ps1 -Install
#>

param(
    [switch]$Loop,
    [int]$IntervalSeconds = 120,
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = "Continue"

$PROJECT_DIR = "C:\Users\mattm\glad-labs-website"
$CLAUDE_EXE = "C:\Users\mattm\.local\bin\claude.exe"
$TELEGRAM_ENV = "$env:USERPROFILE\.claude\channels\telegram\.env"
$LOG_DIR = "$env:USERPROFILE\.claude\logs"
$LOG_FILE = "$LOG_DIR\claude-watchdog.log"
$TASK_NAME = "Claude Code Watchdog"

# Ensure log directory
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts [$Level] $Message"
    Add-Content -Path $LOG_FILE -Value $line
    switch ($Level) {
        "OK"    { Write-Host $line -ForegroundColor Green }
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "ERROR" { Write-Host $line -ForegroundColor Red }
        default { Write-Host $line }
    }
}

# ---- Scheduled Task Install/Uninstall ----

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 2) `
        -RepetitionDuration (New-TimeSpan -Days 365)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask -TaskName $TASK_NAME `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Force

    Write-Log "OK" "Scheduled Task '$TASK_NAME' installed (runs every 2 minutes)"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "OK" "Scheduled Task '$TASK_NAME' removed"
    exit 0
}

# ---- Health Checks ----

function Test-ClaudeProcess {
    # Check for any claude.exe process
    $procs = Get-Process -Name "claude" -ErrorAction SilentlyContinue
    if ($procs -and @($procs).Count -gt 0) {
        return $true
    }

    # Also check for node processes running Claude Code (desktop app variant)
    $nodeProcs = Get-Process -Name "node" -ErrorAction SilentlyContinue |
        Where-Object {
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
                $cmd -and ($cmd -match "claude" -or $cmd -match "@anthropic")
            } catch { $false }
        }
    return ($null -ne $nodeProcs -and @($nodeProcs).Count -gt 0)
}

function Get-TelegramBotToken {
    if (Test-Path $TELEGRAM_ENV) {
        $content = Get-Content $TELEGRAM_ENV -Raw
        if ($content -match "TELEGRAM_BOT_TOKEN=(.+)") {
            return $matches[1].Trim()
        }
    }
    return $null
}

function Test-TelegramBot {
    $token = Get-TelegramBotToken
    if (-not $token) {
        Write-Log "WARN" "No Telegram bot token found in $TELEGRAM_ENV"
        return $null
    }

    try {
        $resp = Invoke-WebRequest -Uri "https://api.telegram.org/bot$token/getMe" `
            -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        $data = $resp.Content | ConvertFrom-Json
        if ($data.ok) {
            return $true
        }
        return $false
    } catch {
        Write-Log "ERROR" "Telegram API check failed: $_"
        return $false
    }
}

function Start-ClaudeCode {
    Write-Log "INFO" "Launching Claude Code in $PROJECT_DIR..."
    try {
        # Launch Claude Code in a new Windows Terminal tab or conhost window
        # Using 'start' to open in a new window so it doesn't block
        $wtExists = Get-Command "wt.exe" -ErrorAction SilentlyContinue

        if ($wtExists) {
            # Windows Terminal: new tab in project dir
            Start-Process "wt.exe" -ArgumentList @(
                "new-tab",
                "--title", "Claude Code (auto-healed)",
                "-d", $PROJECT_DIR,
                "--",
                $CLAUDE_EXE,
                "--continue"
            )
        } else {
            # Fallback: new cmd window
            Start-Process "cmd.exe" -ArgumentList @(
                "/k",
                "cd /d `"$PROJECT_DIR`" && `"$CLAUDE_EXE`" --continue"
            )
        }

        Write-Log "OK" "Claude Code session launched"
        # Give it time to start + connect plugins
        Start-Sleep -Seconds 15
        return $true
    } catch {
        Write-Log "ERROR" "Failed to launch Claude Code: $_"
        return $false
    }
}

# ---- Main Check ----

function Invoke-HealthCheck {
    Write-Log "INFO" "--- Claude Code watchdog check ---"

    # 1. Claude process check
    $processAlive = Test-ClaudeProcess
    if ($processAlive) {
        Write-Log "OK" "Claude Code process running"
    } else {
        Write-Log "ERROR" "Claude Code process NOT running"
    }

    # 2. Telegram bot token validity (doesn't require Claude to be running)
    $botOk = Test-TelegramBot
    if ($null -eq $botOk) {
        Write-Log "WARN" "Telegram bot token not configured"
    } elseif ($botOk) {
        Write-Log "OK" "Telegram bot token valid"
    } else {
        Write-Log "ERROR" "Telegram bot token invalid or API unreachable"
    }

    # 3. Recovery: if Claude not running, start it
    if (-not $processAlive) {
        Write-Log "WARN" "Attempting to restart Claude Code..."
        $started = Start-ClaudeCode

        if ($started -and (Test-ClaudeProcess)) {
            Write-Log "OK" "Claude Code recovered successfully"
        } else {
            Write-Log "ERROR" "Claude Code recovery FAILED - manual intervention needed"
        }
    }

    Write-Log "INFO" "--- Claude Code watchdog check complete ---"
    return $processAlive -or (Test-ClaudeProcess)
}

# ---- Run ----

if ($Loop) {
    Write-Log "INFO" "Claude Code watchdog starting in loop mode (interval: ${IntervalSeconds}s)"
    while ($true) {
        Invoke-HealthCheck
        Start-Sleep -Seconds $IntervalSeconds
    }
} else {
    $result = Invoke-HealthCheck
    exit ([int](-not $result))
}
