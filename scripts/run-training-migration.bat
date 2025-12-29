@echo off
REM Training Data Migration Runner for Windows
REM Runs the training data migration to create all required tables

echo.
echo 🚀 Training Data Migration Runner
echo ==================================
echo.

REM Get database credentials from environment
if not defined DATABASE_USER set DATABASE_USER=postgres
if not defined DATABASE_HOST set DATABASE_HOST=localhost
if not defined DATABASE_PORT set DATABASE_PORT=5432
if not defined DATABASE_NAME set DATABASE_NAME=glad_labs

echo 📍 Database Configuration:
echo    User: %DATABASE_USER%
echo    Host: %DATABASE_HOST%
echo    Port: %DATABASE_PORT%
echo    Database: %DATABASE_NAME%
echo.

REM Check if psql is available
where psql >nul 2>nul
if errorlevel 1 (
    echo ❌ ERROR: psql not found. Please install PostgreSQL.
    echo    Download from: https://www.postgresql.org/download/windows/
    echo    Make sure to add PostgreSQL bin directory to PATH
    echo.
    pause
    exit /b 1
)

echo ✅ PostgreSQL client found
echo.

REM Set migration file path
set MIGRATION_FILE=src\cofounder_agent\migrations\003_training_data_tables.sql

if not exist "%MIGRATION_FILE%" (
    echo ❌ ERROR: Migration file not found: %MIGRATION_FILE%
    echo    Working directory: %cd%
    echo.
    pause
    exit /b 1
)

echo 📌 Migration Details:
echo    File: %MIGRATION_FILE%
echo    Tables: 8 (orchestrator_training_data, training_datasets, fine_tuning_jobs, etc^)
echo    Status: About to execute...
echo.

echo ⏳ Executing migration...
echo.

REM Run the migration
if defined DATABASE_PASSWORD (
    set PGPASSWORD=%DATABASE_PASSWORD%
    psql -U %DATABASE_USER% -h %DATABASE_HOST% -p %DATABASE_PORT% -d %DATABASE_NAME% -f %MIGRATION_FILE%
) else (
    psql -U %DATABASE_USER% -h %DATABASE_HOST% -p %DATABASE_PORT% -d %DATABASE_NAME% -f %MIGRATION_FILE%
)

if errorlevel 1 (
    echo.
    echo ❌ Migration failed!
    echo.
    echo Troubleshooting:
    echo   - Check if PostgreSQL is running
    echo   - Verify database credentials (DATABASE_USER, DATABASE_PASSWORD, etc^)
    echo   - Try connecting manually: psql -U %DATABASE_USER% -h %DATABASE_HOST% -d %DATABASE_NAME%
    echo.
    pause
    exit /b 1
)

echo.
echo ✅ Migration completed successfully!
echo.

REM Verify tables
echo 📊 Verifying tables...
if defined DATABASE_PASSWORD (
    set PGPASSWORD=%DATABASE_PASSWORD%
    psql -U %DATABASE_USER% -h %DATABASE_HOST% -p %DATABASE_PORT% -d %DATABASE_NAME% -c "\dt orchestrator_* training_* fine_tuning_* learning_* social_* web_analytics financial_*"
) else (
    psql -U %DATABASE_USER% -h %DATABASE_HOST% -p %DATABASE_PORT% -d %DATABASE_NAME% -c "\dt orchestrator_* training_* fine_tuning_* learning_* social_* web_analytics financial_*"
)

echo.
echo 🎉 Training system database is ready!
echo.
echo Next steps:
echo   1. Configure environment variables ^(see QUICK_INTEGRATION_GUIDE.md Step 3^)
echo   2. Start the backend: npm start
echo   3. Test API endpoints: curl http://localhost:8000/api/orchestrator/training/stats
echo.

pause
