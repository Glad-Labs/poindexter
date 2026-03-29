<#
.SYNOPSIS
    Syncs OpenClaw config from git template + secrets into ~/.openclaw/openclaw.json

.DESCRIPTION
    Reads infrastructure/openclaw/openclaw.json.template from the project,
    substitutes secrets from ~/.openclaw/workspace/.env, and writes the
    final openclaw.json to ~/.openclaw/.

    Secrets mapping (from .env):
      ANTHROPIC_API_KEY, GEMINI_API_KEY, NOTION_API_KEY, ELEVENLABS_API_KEY,
      DISCORD_BOT_TOKEN (used via env, not inlined), TELEGRAM_BOT_TOKEN
      (derived from existing openclaw.json if not in .env), GOPLACES_API_KEY

.EXAMPLE
    .\sync-config.ps1
    .\sync-config.ps1 -DryRun   # Show what would change without writing
#>

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Get-Item "$PSScriptRoot\..\..")
$TemplatePath = "$PSScriptRoot\openclaw.json.template"
$EnvPath = "$env:USERPROFILE\.openclaw\workspace\.env"
$OutputPath = "$env:USERPROFILE\.openclaw\openclaw.json"
$BackupPath = "$env:USERPROFILE\.openclaw\openclaw.json.bak"

# --- Load secrets from .env ---
function Read-EnvFile($Path) {
    $vars = @{}
    if (-not (Test-Path $Path)) {
        Write-Error ".env file not found at $Path"
        return $vars
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                $vars[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
    }
    return $vars
}

# --- Read Telegram bot token from current config (not in .env) ---
function Get-TelegramToken {
    if (Test-Path $OutputPath) {
        $current = Get-Content $OutputPath -Raw | ConvertFrom-Json
        return $current.channels.telegram.botToken
    }
    return $null
}

# --- Read Google Places API key from current config (not in .env as GOPLACES_API_KEY) ---
function Get-GoPlacesKey {
    if (Test-Path $OutputPath) {
        $current = Get-Content $OutputPath -Raw | ConvertFrom-Json
        return $current.skills.entries.goplaces.apiKey
    }
    return $null
}

Write-Host "OpenClaw Config Sync" -ForegroundColor Cyan
Write-Host "====================" -ForegroundColor Cyan

# Load template
if (-not (Test-Path $TemplatePath)) {
    Write-Error "Template not found: $TemplatePath"
    exit 1
}
$template = Get-Content $TemplatePath -Raw

# Load secrets
$secrets = Read-EnvFile $EnvPath
Write-Host "Loaded $($secrets.Count) vars from .env"

# Build substitution map
$telegramToken = $secrets["TELEGRAM_BOT_TOKEN"]
if (-not $telegramToken) { $telegramToken = Get-TelegramToken }

$goplacesKey = $secrets["GOPLACES_API_KEY"]
if (-not $goplacesKey) { $goplacesKey = Get-GoPlacesKey }

$substitutions = @{
    '${ANTHROPIC_API_KEY}'   = $secrets["ANTHROPIC_API_KEY"]
    '${GEMINI_API_KEY}'      = $secrets["GEMINI_API_KEY"]
    '${NOTION_API_KEY}'      = $secrets["NOTION_API_KEY"]
    '${ELEVENLABS_API_KEY}'  = $secrets["ELEVENLABS_API_KEY"]
    '${TELEGRAM_BOT_TOKEN}'  = $telegramToken
    '${GOPLACES_API_KEY}'    = $goplacesKey
}

# Verify all secrets resolved
$missing = @()
foreach ($kv in $substitutions.GetEnumerator()) {
    if (-not $kv.Value) {
        $missing += $kv.Key
    }
}
if ($missing.Count -gt 0) {
    Write-Warning "Missing secrets (will be left as placeholders): $($missing -join ', ')"
}

# Apply substitutions
$output = $template
foreach ($kv in $substitutions.GetEnumerator()) {
    if ($kv.Value) {
        $output = $output.Replace($kv.Key, $kv.Value)
    }
}

if ($DryRun) {
    Write-Host "`n[DRY RUN] Would write to: $OutputPath" -ForegroundColor Yellow
    Write-Host "Substituted keys: $($substitutions.Keys | Where-Object { $substitutions[$_] } | ForEach-Object { $_ })"
    exit 0
}

# Backup existing
if (Test-Path $OutputPath) {
    Copy-Item $OutputPath $BackupPath -Force
    Write-Host "Backed up existing config to openclaw.json.bak"
}

# Write
$output | Set-Content $OutputPath -Encoding UTF8 -NoNewline
Write-Host "Config written to $OutputPath" -ForegroundColor Green
Write-Host "Done!" -ForegroundColor Green
