#!/usr/bin/env powershell
# =============================================================================
# Local PostgreSQL Setup Script for Glad Labs Development
# =============================================================================
# This script sets up PostgreSQL with the glad_labs_dev database
# Usage: .\setup-postgres.ps1
# =============================================================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Glad Labs - PostgreSQL Setup Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if PostgreSQL is installed
Write-Host "Checking PostgreSQL installation..." -ForegroundColor Yellow
try {
    $psqlVersion = psql --version 2>$null
    if ($psqlVersion) {
        Write-Host "✅ PostgreSQL found: $psqlVersion" -ForegroundColor Green
    } else {
        Write-Host "❌ PostgreSQL not found in PATH" -ForegroundColor Red
        Write-Host "Please install PostgreSQL and add it to PATH" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "❌ PostgreSQL not found" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Connecting to PostgreSQL..." -ForegroundColor Yellow

# Create database glad_labs_dev
Write-Host "Creating database 'glad_labs_dev'..." -ForegroundColor Yellow

$sqlScript = @"
-- Drop existing database (optional - uncomment if needed)
-- DROP DATABASE IF EXISTS glad_labs_dev;

-- Create database
CREATE DATABASE IF NOT EXISTS glad_labs_dev;

-- Create user (optional if postgres user already exists)
-- CREATE USER glad_labs_dev WITH ENCRYPTED PASSWORD 'postgres';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE glad_labs_dev TO postgres;

-- Display result
\l
"@

# Execute SQL
$sqlScript | psql -U postgres

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Database setup complete" -ForegroundColor Green
} else {
    Write-Host "❌ Database setup failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Verify .env.local has DATABASE_URL set" -ForegroundColor White
Write-Host "2. Run: npm run setup:all" -ForegroundColor White
Write-Host "3. Run: npm run dev" -ForegroundColor White
Write-Host ""
Write-Host "Verify connection:" -ForegroundColor Yellow
Write-Host "  psql -U postgres -d glad_labs_dev" -ForegroundColor White
Write-Host ""
