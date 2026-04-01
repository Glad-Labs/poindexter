<#
.SYNOPSIS
    Glad Labs Production Database Backup Script (PostgreSQL on Railway)

.DESCRIPTION
    Creates compressed pg_dump backups of the Railway PostgreSQL database.
    Saves to C:\Users\mattm\.gladlabs\backups\ with timestamped filenames.
    Retains the last 7 daily backups and deletes older ones.
    Designed to run as a Windows Scheduled Task.

.EXAMPLE
    # Manual run:
    .\scripts\db-backup.ps1

    # Schedule via Task Scheduler (run daily at 2 AM):
    # Action: powershell.exe
    # Arguments: -ExecutionPolicy Bypass -File "C:\Users\mattm\glad-labs-website\scripts\db-backup.ps1"

.NOTES
    Requires: PostgreSQL client tools (pg_dump) in PATH or at standard install location.
    Database: Railway PostgreSQL (public proxy)
#>

[CmdletBinding()]
param(
    [string]$BackupDir = "C:\Users\mattm\.gladlabs\backups",
    [int]$RetentionDays = 7,
    [switch]$SchemaOnly,
    [switch]$DataOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Configuration ---
$DB_HOST = "hopper.proxy.rlwy.net"
$DB_PORT = "32382"
$DB_NAME = "railway"
$DB_USER = "postgres"
$DB_PASS = "***REMOVED***"

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$BackupFileName = "gladlabs-db-${Timestamp}.dump"
$LogFile = Join-Path $BackupDir "backup.log"

# --- Helper: Write to log and console ---
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $entry = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -ErrorAction SilentlyContinue
}

# --- Ensure backup directory exists ---
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Write-Log "Created backup directory: $BackupDir"
}

# --- Locate pg_dump ---
$pgDump = $null

# Check PATH first
$pgDumpPath = Get-Command pg_dump -ErrorAction SilentlyContinue
if ($pgDumpPath) {
    $pgDump = $pgDumpPath.Source
}

# Fallback: standard PostgreSQL install locations
if (-not $pgDump) {
    $searchPaths = @(
        "C:\Program Files\PostgreSQL\*\bin\pg_dump.exe",
        "C:\Program Files (x86)\PostgreSQL\*\bin\pg_dump.exe"
    )
    foreach ($pattern in $searchPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) {
            $pgDump = $found.FullName
            break
        }
    }
}

if (-not $pgDump) {
    Write-Log "pg_dump not found. Install PostgreSQL client tools or add pg_dump to PATH." "ERROR"
    exit 1
}

Write-Log "Using pg_dump: $pgDump"

# --- Build pg_dump arguments ---
$BackupPath = Join-Path $BackupDir $BackupFileName

$pgArgs = @(
    "--host=$DB_HOST",
    "--port=$DB_PORT",
    "--username=$DB_USER",
    "--dbname=$DB_NAME",
    "--format=custom",       # Compressed custom format (use pg_restore to restore)
    "--verbose",
    "--file=$BackupPath"
)

if ($SchemaOnly) {
    $pgArgs += "--schema-only"
    Write-Log "Mode: schema-only backup"
} elseif ($DataOnly) {
    $pgArgs += "--data-only"
    Write-Log "Mode: data-only backup"
}

# --- Run pg_dump ---
Write-Log "Starting backup of $DB_NAME@${DB_HOST}:${DB_PORT}..."
Write-Log "Output file: $BackupPath"

$env:PGPASSWORD = $DB_PASS

try {
    $process = Start-Process -FilePath $pgDump -ArgumentList $pgArgs `
        -NoNewWindow -Wait -PassThru -RedirectStandardError (Join-Path $BackupDir "pg_dump_stderr.tmp")

    $stderrContent = ""
    $stderrFile = Join-Path $BackupDir "pg_dump_stderr.tmp"
    if (Test-Path $stderrFile) {
        $stderrContent = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
        Remove-Item $stderrFile -ErrorAction SilentlyContinue
    }

    if ($process.ExitCode -ne 0) {
        Write-Log "pg_dump failed with exit code $($process.ExitCode)" "ERROR"
        if ($stderrContent) {
            Write-Log "pg_dump stderr: $stderrContent" "ERROR"
        }
        # Clean up failed backup file
        if (Test-Path $BackupPath) { Remove-Item $BackupPath -ErrorAction SilentlyContinue }
        exit 1
    }

    # Verify file was created and has content
    if (-not (Test-Path $BackupPath)) {
        Write-Log "Backup file was not created" "ERROR"
        exit 1
    }

    $fileSize = (Get-Item $BackupPath).Length
    if ($fileSize -eq 0) {
        Write-Log "Backup file is empty (0 bytes)" "ERROR"
        Remove-Item $BackupPath -ErrorAction SilentlyContinue
        exit 1
    }

    $sizeMB = [math]::Round($fileSize / 1MB, 2)
    Write-Log "Backup successful: $BackupFileName ($sizeMB MB)"

} catch {
    Write-Log "Exception during backup: $_" "ERROR"
    if (Test-Path $BackupPath) { Remove-Item $BackupPath -ErrorAction SilentlyContinue }
    exit 1
} finally {
    $env:PGPASSWORD = $null
}

# --- Retention: delete backups older than $RetentionDays days ---
Write-Log "Applying retention policy: keeping last $RetentionDays days of backups..."

$cutoffDate = (Get-Date).AddDays(-$RetentionDays)
$oldBackups = Get-ChildItem -Path $BackupDir -Filter "gladlabs-db-*.dump" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoffDate }

if ($oldBackups) {
    foreach ($old in $oldBackups) {
        Remove-Item $old.FullName -Force
        Write-Log "Deleted old backup: $($old.Name)"
    }
    Write-Log "Deleted $($oldBackups.Count) old backup(s)"
} else {
    Write-Log "No backups older than $RetentionDays days to clean up"
}

# --- Summary ---
$remainingBackups = @(Get-ChildItem -Path $BackupDir -Filter "gladlabs-db-*.dump" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
$totalSizeMB = [math]::Round(($remainingBackups | Measure-Object -Property Length -Sum).Sum / 1MB, 2)

Write-Log "=== Backup Summary ==="
Write-Log "  Latest: $BackupFileName ($sizeMB MB)"
Write-Log "  Total backups: $($remainingBackups.Count)"
Write-Log "  Total size: $totalSizeMB MB"
Write-Log "  Location: $BackupDir"
Write-Log "======================"

exit 0
