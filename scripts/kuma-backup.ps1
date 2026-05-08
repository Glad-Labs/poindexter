<#
.SYNOPSIS
    Hourly Kuma SQLite backup. Tier-1 host-side, sits alongside the
    in-container poindexter-backup-hourly (which only does pg_dump).

.DESCRIPTION
    Closes the gap discovered 2026-05-07 — Kuma's volume was wiped at
    some point and no auto-backup existed because the backup container
    was Postgres-only. After Kuma was rebuilt by hand, this script
    snapshots the SQLite DB once an hour into ${BACKUP_DIR}/kuma/ via
    SQLite's online .backup command (WAL-safe). Prunes to the configured
    retention.

    Uses `docker exec` against the kuma container, so it works with any
    Kuma deployment as long as the container name matches.

    Modes:
      - One-shot:  .\kuma-backup.ps1
      - Install:   .\kuma-backup.ps1 -Install   (hourly scheduled task)
      - Uninstall: .\kuma-backup.ps1 -Uninstall
#>

param(
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Retention = 48,
    [string]$BackupRoot = "$env:USERPROFILE\.poindexter\backups\auto"
)

$ErrorActionPreference = "Continue"

$CONTAINER = "poindexter-uptime-kuma"
$KUMA_DB   = "/app/data/kuma.db"
$STAGED    = "/tmp/kuma-backup.db"
$LOG_DIR   = "$env:USERPROFILE\.poindexter\logs"
$LOG_FILE  = "$LOG_DIR\kuma-backup.log"
$TASK_NAME = "Kuma SQLite Backup"

if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "$ts [$Level] $Message"
}

# ---- Install / Uninstall ----

if ($Install) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Hours 1) `
        -RepetitionDuration (New-TimeSpan -Days 365)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask -TaskName $TASK_NAME `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Force | Out-Null

    Write-Log "OK" "Scheduled Task '$TASK_NAME' installed (hourly)"
    Write-Host "Installed scheduled task: $TASK_NAME (hourly)"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "OK" "Scheduled Task '$TASK_NAME' removed"
    exit 0
}

# ---- One-shot backup ----

$kumaBackupDir = Join-Path $BackupRoot 'kuma'
if (-not (Test-Path $kumaBackupDir)) {
    New-Item -ItemType Directory -Path $kumaBackupDir -Force | Out-Null
}

# Verify kuma container is running
$status = docker inspect --format '{{.State.Status}}' $CONTAINER 2>&1
if ($LASTEXITCODE -ne 0 -or $status -ne 'running') {
    Write-Log "WARN" "Container $CONTAINER not running (status=$status) - skipping"
    exit 0
}

$ts = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$destFile = Join-Path $kumaBackupDir "kuma_$ts.db"

# 1. SQLite online backup inside the container — WAL-safe even with kuma running.
#    Path uses double-leading-slash to avoid git-bash MSYS path mangling.
$dumpResult = docker exec $CONTAINER sqlite3 //app/data/kuma.db ".backup $STAGED" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR" "sqlite3 .backup failed: $dumpResult"
    exit 1
}

# 2. Copy out to host backup directory.
$copyResult = docker cp "${CONTAINER}:${STAGED}" $destFile 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR" "docker cp failed: $copyResult"
    exit 1
}

# 3. Tidy up the staged file inside the container.
docker exec $CONTAINER rm -f $STAGED 2>&1 | Out-Null

# 4. Verify backup is non-trivially sized + valid SQLite.
$sizeBytes = (Get-Item $destFile).Length
$header = [System.IO.File]::ReadAllBytes($destFile)[0..14]
$headerStr = [System.Text.Encoding]::ASCII.GetString($header)
$validHeader = $headerStr.StartsWith('SQLite format 3')
if (-not $validHeader -or $sizeBytes -lt 1024) {
    Write-Log "ERROR" "Backup looks invalid (size=$sizeBytes header_ok=$validHeader)"
    Remove-Item $destFile -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Log "OK" "Wrote $destFile ($sizeBytes bytes)"

# 5. Prune to retention.
$kept = Get-ChildItem $kumaBackupDir -Filter 'kuma_*.db' |
    Sort-Object LastWriteTime -Descending
$toDelete = $kept | Select-Object -Skip $Retention
foreach ($f in $toDelete) {
    Write-Log "INFO" "Pruning $($f.FullName)"
    Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
}

exit 0
