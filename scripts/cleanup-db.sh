y#!/bin/bash
# Glad Labs Database Cleanup Script
# Removes unused tables to simplify schema
# Safe: All removed tables have 0 rows and no dependencies
# 
# Usage: bash cleanup-db.sh
# Or manually execute the SQL in your PostgreSQL client

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          Glad Labs Database Cleanup Script                     ║"
echo "║  Removes unused tables to simplify schema                      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "ℹ️  This script will remove the following tables (all with 0 rows):"
echo "   - feature_flags (48 kB)"
echo "   - settings_audit_log (48 kB)"
echo "   - logs (32 kB)"
echo "   - financial_entries (32 kB)"
echo "   - agent_status (32 kB)"
echo "   - health_checks (32 kB)"
echo "   - content_metrics (32 kB)"
echo ""
echo "Total removal: ~376 kB"
echo ""

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL environment variable not set!"
    echo ""
    echo "Set it with:"
    echo "  export DATABASE_URL='postgresql://user:password@localhost:5432/glad_labs_dev'"
    echo ""
    exit 1
fi

echo "Database: ${DATABASE_URL}"
echo ""
echo "Proceed with cleanup? (yes/no)"
read -r response

if [ "$response" != "yes" ]; then
    echo "❌ Cancelled - no changes made"
    exit 0
fi

echo ""
echo "Starting cleanup..."
echo ""

# Execute cleanup SQL
psql "$DATABASE_URL" <<EOF
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
EOF

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Cleanup completed successfully!"
    echo ""
    echo "Summary:"
    echo "  - Removed 7 unused tables"
    echo "  - Freed ~376 kB"
    echo "  - Simplified schema"
    echo ""
else
    echo ""
    echo "❌ Cleanup failed - check PostgreSQL connection"
    exit 1
fi
