<#
.SYNOPSIS
    Start Claude Code with Telegram channel and skip-permissions on boot.
    Waits for Docker to be ready first.
#>

$ErrorActionPreference = "Stop"

# Wait for Docker to be ready
$maxWait = 120
$waited = 0
while ($waited -lt $maxWait) {
    try {
        $result = docker info 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Docker is ready after ${waited}s"
            break
        }
    } catch {}
    Start-Sleep -Seconds 5
    $waited += 5
    Write-Host "Waiting for Docker... (${waited}s)"
}

if ($waited -ge $maxWait) {
    Write-Host "Docker not ready after ${maxWait}s — starting Claude Code anyway"
}

# Wait a bit more for containers to start
Start-Sleep -Seconds 15

# Start Claude Code with Telegram channel
Set-Location "C:\Users\mattm\glad-labs-website"
claude --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions
