<#
.SYNOPSIS
    Glad Labs Database Restore Script

.DESCRIPTION
    Restores a PostgreSQL backup created by db-backup.ps1 to the Railway database
    or to a local PostgreSQL instance for testing.

    CAUTION: Restoring to the production Railway database will OVERWRITE existing data.
    Use -WhatIf to preview without executing, or restore to a local database first.

.PARAMETER BackupFile
    Path to the .dump backup file created by db-backup.ps1.

.PARAMETER TargetHost
    Database host to restore to. Defaults to the Railway public proxy.

.PARAMETER TargetPort
    Database port. Defaults to 32382 (Railway).

.PARAMETER TargetDB
    Database name. Defaults to 'railway'.

.PARAMETER TargetUser
    Database user. Defaults to 'postgres'.

.PARAMETER TargetPassword
    Database password. Will prompt if not provided.

.PARAMETER Local
    Shortcut to restore to localhost:5432/glad_labs_dev (local development database).

.PARAMETER CleanRestore
    Drop and recreate objects before restoring (--clean --if-exists flags).

.EXAMPLE
    # Restore to local dev database:
    .\scripts\db-restore.ps1 -BackupFile "C:\Users\mattm\.gladlabs\backups\gladlabs-db-2026-03-31_020000.dump" -Local

    # Restore to production (use with extreme caution):
    .\scripts\db-restore.ps1 -BackupFile "C:\Users\mattm\.gladlabs\backups\gladlabs-db-2026-03-31_020000.dump"

.NOTES
    Requires: PostgreSQL client tools (pg_restore) in PATH or at standard install location.
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateScript({ Test-Path $_ -PathType Leaf })]
    [string]$BackupFile,

    [string]$TargetHost = "hopper.proxy.rlwy.net",
    [string]$TargetPort = "32382",
    [string]$TargetDB = "gladlabs_brain",
    [string]$TargetUser = "postgres",
    [string]$TargetPassword = $env:DB_PASS,

    [switch]$Local,
    [switch]$CleanRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Override target if -Local is set ---
if ($Local) {
    $TargetHost = "localhost"
    $TargetPort = "5432"
    $TargetDB = "glad_labs_dev"
    $TargetUser = "postgres"
    if (-not $TargetPassword) {
        $TargetPassword = "postgres"
    }
}

# --- Locate pg_restore ---
$pgRestore = $null

$pgRestorePath = Get-Command pg_restore -ErrorAction SilentlyContinue
if ($pgRestorePath) {
    $pgRestore = $pgRestorePath.Source
}

if (-not $pgRestore) {
    $searchPaths = @(
        "C:\Program Files\PostgreSQL\*\bin\pg_restore.exe",
        "C:\Program Files (x86)\PostgreSQL\*\bin\pg_restore.exe"
    )
    foreach ($pattern in $searchPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) {
            $pgRestore = $found.FullName
            break
        }
    }
}

if (-not $pgRestore) {
    Write-Error "pg_restore not found. Install PostgreSQL client tools or add pg_restore to PATH."
    exit 1
}

# --- Confirmation prompt for production restores ---
$fileSizeMB = [math]::Round((Get-Item $BackupFile).Length / 1MB, 2)

Write-Host ""
Write-Host "=== Glad Labs Database Restore ===" -ForegroundColor Cyan
Write-Host "  Backup file : $BackupFile ($fileSizeMB MB)"
Write-Host "  Target      : ${TargetUser}@${TargetHost}:${TargetPort}/${TargetDB}"
Write-Host "  Clean mode  : $CleanRestore"
Write-Host ""

if (-not $Local) {
    Write-Host "WARNING: You are about to restore to the PRODUCTION database!" -ForegroundColor Red
    Write-Host "This will modify data on $TargetHost." -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "Type 'RESTORE' to confirm, or anything else to cancel"
    if ($confirm -ne "RESTORE") {
        Write-Host "Restore cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# --- Build pg_restore arguments ---
$pgArgs = @(
    "--host=$TargetHost",
    "--port=$TargetPort",
    "--username=$TargetUser",
    "--dbname=$TargetDB",
    "--verbose",
    "--no-owner",           # Don't try to set object ownership
    "--no-privileges"       # Don't restore access privileges
)

if ($CleanRestore) {
    $pgArgs += "--clean"
    $pgArgs += "--if-exists"
}

$pgArgs += $BackupFile

# --- Run pg_restore ---
Write-Host "Starting restore..." -ForegroundColor Green

$env:PGPASSWORD = $TargetPassword

try {
    $stderrFile = [System.IO.Path]::GetTempFileName()
    $process = Start-Process -FilePath $pgRestore -ArgumentList $pgArgs `
        -NoNewWindow -Wait -PassThru -RedirectStandardError $stderrFile

    $stderrContent = ""
    if (Test-Path $stderrFile) {
        $stderrContent = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
        Remove-Item $stderrFile -ErrorAction SilentlyContinue
    }

    # pg_restore exit code 0 = success, 1 = warnings (some objects may already exist)
    if ($process.ExitCode -eq 0) {
        Write-Host "Restore completed successfully." -ForegroundColor Green
    } elseif ($process.ExitCode -eq 1) {
        Write-Host "Restore completed with warnings (some objects may have already existed)." -ForegroundColor Yellow
        if ($stderrContent) {
            # Show only error/warning lines, not verbose output
            $warnings = ($stderrContent -split "`n") | Where-Object { $_ -match "ERROR|WARNING" }
            if ($warnings) {
                Write-Host "Warnings:" -ForegroundColor Yellow
                $warnings | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
            }
        }
    } else {
        Write-Host "Restore failed with exit code $($process.ExitCode)" -ForegroundColor Red
        if ($stderrContent) {
            Write-Host "Error output:" -ForegroundColor Red
            Write-Host $stderrContent
        }
        exit 1
    }

} catch {
    Write-Host "Exception during restore: $_" -ForegroundColor Red
    exit 1
} finally {
    $env:PGPASSWORD = $null
}

Write-Host ""
Write-Host "Restore target: ${TargetUser}@${TargetHost}:${TargetPort}/${TargetDB}" -ForegroundColor Cyan
Write-Host "Done." -ForegroundColor Green

exit 0
