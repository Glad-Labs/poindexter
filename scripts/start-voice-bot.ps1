# Load voice bot token from OpenClaw .env (not hardcoded)
$envFile = "$env:USERPROFILE\.openclaw\workspace\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line -split "=", 2
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
}
if (-not $env:DISCORD_VOICE_BOT_TOKEN) {
    $env:DISCORD_VOICE_BOT_TOKEN = $env:DISCORD_BOT_TOKEN
}
Start-Process pythonw.exe -ArgumentList "C:\Users\mattm\glad-labs-website\scripts\discord_voice_bot.py" -WindowStyle Hidden
Write-Output "Voice bot started"
