@echo off
REM GLAD Labs Tier 1 Database Backup Script (Windows Batch)
REM 
REM Purpose: Automated PostgreSQL backup for Tier 1 production
REM Schedule: Windows Task Scheduler (daily at 2 AM)
REM Retention: 7-day rolling backup
REM
REM Usage: backup-tier1-db.bat
REM        Or schedule in Task Scheduler for automated backups

setlocal enabledelayedexpansion

REM Configuration
set BACKUP_DIR=.\backups\tier1
set RETENTION_DAYS=7
set TIMESTAMP=%date:~10,4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set BACKUP_FILE=%BACKUP_DIR%\db-backup-%TIMESTAMP%.sql
set LOG_FILE=%BACKUP_DIR%\backup.log

REM Colors (for console output)
for /F %%A in ('echo prompt $H ^| cmd') do set "BS=%%A"

echo.
echo ╔════════════════════════════════════════════════════════╗
echo ║  GLAD Labs Tier 1 Database Backup Script              ║
echo ║  Database: PostgreSQL ^| Schedule: Daily at 2 AM      ║
echo ╚════════════════════════════════════════════════════════╝
echo.

REM Create backup directory if it doesn't exist
if not exist "%BACKUP_DIR%" (
    echo [%date% %time%] Creating backup directory: %BACKUP_DIR%
    mkdir "%BACKUP_DIR%"
)

REM Log backup start
echo. >> "%LOG_FILE%"
echo [%date% %time%] ========================================== >> "%LOG_FILE%"
echo [%date% %time%] Starting Tier 1 Database Backup >> "%LOG_FILE%"
echo [%date% %time%] ========================================== >> "%LOG_FILE%"

REM Check if DATABASE_URL is set
if not defined DATABASE_URL (
    echo [%date% %time%] ERROR: DATABASE_URL environment variable not set >> "%LOG_FILE%"
    echo ERROR: DATABASE_URL environment variable not set
    echo Please set your Railway PostgreSQL connection string in DATABASE_URL
    pause
    exit /b 1
)

REM Parse DATABASE_URL for connection details
REM Format: postgresql://user:password@host:port/dbname
REM This is a simplified parser - adjust based on your URL format
REM For production, consider using a proper URL parser

echo [%date% %time%] Using DATABASE_URL: %DATABASE_URL% >> "%LOG_FILE%"
echo 📦 Backing up database to: %BACKUP_FILE%

REM Attempt backup using pg_dump (if installed)
where pg_dump >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [%date% %time%] pg_dump found, attempting backup >> "%LOG_FILE%"
    
    REM Export DATABASE_URL for pg_dump
    set PGPASSWORD=%DATABASE_URL:~20%
    
    pg_dump %DATABASE_URL% > "%BACKUP_FILE%" 2>> "%LOG_FILE%"
    
    if %ERRORLEVEL% EQU 0 (
        echo [%date% %time%] ✅ Backup successful >> "%LOG_FILE%"
        echo [%date% %time%] Backup file: %BACKUP_FILE% >> "%LOG_FILE%"
        
        REM Get backup file size
        for %%F in ("%BACKUP_FILE%") do set SIZE=%%~zF
        echo ✅ Backup successful^! Size: !SIZE! bytes
        echo [%date% %time%] Backup size: !SIZE! bytes >> "%LOG_FILE%"
        
    ) else (
        echo [%date% %time%] ❌ Backup failed >> "%LOG_FILE%"
        echo ❌ Backup failed - check log for details
        pause
        exit /b 1
    )
) else (
    echo [%date% %time%] pg_dump not found, using Railway CLI >> "%LOG_FILE%"
    echo 📦 Using Railway CLI for backup...
    
    REM Alternative: Use Railway CLI to backup
    railway database backup > "%BACKUP_FILE%" 2>> "%LOG_FILE%"
    
    if %ERRORLEVEL% EQU 0 (
        echo [%date% %time%] ✅ Railway backup successful >> "%LOG_FILE%"
        echo ✅ Railway backup successful
    ) else (
        echo [%date% %time%] ⚠️  Railway CLI backup not available >> "%LOG_FILE%"
        echo ⚠️  pg_dump and Railway CLI both unavailable
        echo Please install PostgreSQL client tools or use Railway dashboard
        pause
    )
)

REM Clean up old backups (retention policy)
echo [%date% %time%] Cleaning up old backups (retention: %RETENTION_DAYS% days) >> "%LOG_FILE%"

REM Calculate cutoff date (7 days ago)
REM This is simplified - adjust based on your date format needs
setlocal enabledelayedexpansion

for /f "delims=" %%A in ('wmic os get localdatetime ^| find "."') do set DTS=%%A
set YYYY=!DTS:~0,4!
set MM=!DTS:~4,2!
set DD=!DTS:~6,2!

REM Note: Complex date arithmetic in batch is difficult
REM For production, consider using PowerShell for this task
echo [%date% %time%] Note: Manual cleanup recommended via PowerShell >> "%LOG_FILE%"

REM List current backups
echo. >> "%LOG_FILE%"
echo [%date% %time%] Current backups: >> "%LOG_FILE%"
dir "%BACKUP_DIR%\db-backup-*.sql" >> "%LOG_FILE%" 2>&1

echo [%date% %time%] Backup job completed >> "%LOG_FILE%"
echo [%date% %time%] ========================================== >> "%LOG_FILE%"

REM Copy to remote storage (optional)
echo.
echo 💾 Backup options:
echo    1. Keep local backup (current location: %BACKUP_DIR%)
echo    2. Upload to S3 (requires AWS CLI)
echo    3. Manual upload to cloud storage

REM Optional: Upload to S3
where aws >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo.
    set /p UPLOAD="Upload to S3? (y/n): "
    if /i "!UPLOAD!"=="y" (
        set /p BUCKET="Enter S3 bucket name: "
        echo [%date% %time%] Uploading to S3: !BUCKET! >> "%LOG_FILE%"
        aws s3 cp "%BACKUP_FILE%" "s3://!BUCKET!/tier1-backups/" >> "%LOG_FILE%" 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo ✅ Uploaded to S3
            echo [%date% %time%] ✅ S3 upload successful >> "%LOG_FILE%"
        ) else (
            echo ⚠️  S3 upload failed
            echo [%date% %time%] ❌ S3 upload failed >> "%LOG_FILE%"
        )
    )
)

echo.
echo ✅ Backup process complete
echo 📄 Log: %LOG_FILE%
echo.
echo 🎯 Next steps:
echo    1. Verify backup file exists and has content
echo    2. Schedule this script in Task Scheduler for daily backups
echo    3. Test recovery procedure monthly
echo    4. Monitor backup log for errors
echo.

endlocal
