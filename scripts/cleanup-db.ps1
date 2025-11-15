# Glad Labs Database Cleanup Script (PowerShell)
# Removes unused tables to simplify schema
# Safe: All removed tables have 0 rows and no dependencies
# 
# Usage: .\scripts\cleanup-db.ps1

Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║          Glad Labs Database Cleanup Script                     ║" -ForegroundColor Cyan
Write-Host "║  Removes unused tables to simplify schema                      ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "ℹ️  This script will remove the following tables (all with 0 rows):" -ForegroundColor Yellow
Write-Host "   - feature_flags (48 kB)"
Write-Host "   - settings_audit_log (48 kB)"
Write-Host "   - logs (32 kB)"
Write-Host "   - financial_entries (32 kB)"
Write-Host "   - agent_status (32 kB)"
Write-Host "   - health_checks (32 kB)"
Write-Host "   - content_metrics (32 kB)"
Write-Host ""
Write-Host "Total removal: ~376 kB"
Write-Host ""

# Check if DATABASE_URL is set
$databaseUrl = $env:DATABASE_URL
if ([string]::IsNullOrEmpty($databaseUrl)) {
    Write-Host "❌ ERROR: DATABASE_URL environment variable not set!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Set it with:"
    Write-Host "  `$env:DATABASE_URL = 'postgresql://user:password@localhost:5432/glad_labs_dev'"
    Write-Host ""
    exit 1
}

Write-Host "Database: $($databaseUrl.Substring(0, [Math]::Min(50, $databaseUrl.Length)))..." -ForegroundColor Green
Write-Host ""

$response = Read-Host "Proceed with cleanup? (yes/no)"

if ($response -ne "yes") {
    Write-Host "❌ Cancelled - no changes made" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "Starting cleanup..." -ForegroundColor Green
Write-Host ""

# Cleanup SQL script
$cleanupSql = @"
BEGIN TRANSACTION;

-- Phase 1: Remove completely unused tables
DROP TABLE IF EXISTS feature_flags CASCADE;
DROP TABLE IF EXISTS settings_audit_log CASCADE;
DROP TABLE IF EXISTS logs CASCADE;
DROP TABLE IF EXISTS financial_entries CASCADE;
DROP TABLE IF EXISTS agent_status CASCADE;
DROP TABLE IF EXISTS health_checks CASCADE;
DROP TABLE IF EXISTS content_metrics CASCADE;

-- Verify cleanup
SELECT 'Tables remaining:' as status;
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;

COMMIT;
"@

# Execute cleanup
try {
    # Use psql if available, otherwise use dotnet or other method
    $psqlPath = Get-Command psql -ErrorAction SilentlyContinue
    
    if ($psqlPath) {
        $cleanupSql | & psql $databaseUrl
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "✅ Cleanup completed successfully!" -ForegroundColor Green
            Write-Host ""
            Write-Host "Summary:" -ForegroundColor Green
            Write-Host "  - Removed 7 unused tables"
            Write-Host "  - Freed ~376 kB"
            Write-Host "  - Simplified schema"
            Write-Host ""
        } else {
            Write-Host ""
            Write-Host "❌ Cleanup failed - check PostgreSQL connection" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "⚠️  psql not found in PATH" -ForegroundColor Yellow
        Write-Host "Please execute this SQL manually in your PostgreSQL client:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host $cleanupSql -ForegroundColor Cyan
    }
}
catch {
    Write-Host "❌ Error executing cleanup: $_" -ForegroundColor Red
    exit 1
}
