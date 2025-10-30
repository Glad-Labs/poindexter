-- Migration: Fix Duplicate Index Names
-- Purpose: Rename duplicate index names across multiple tables
-- Status: Safe to run (uses IF EXISTS)
-- Created: 2025-10-30

-- Drop old duplicate indexes (PostgreSQL will handle missing ones gracefully)
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

-- Verify new indexes exist (created by SQLAlchemy models)
-- These should be automatically created by the application on next startup
SELECT 
    indexname,
    tablename
FROM pg_indexes
WHERE indexname LIKE 'idx_%' 
ORDER BY tablename, indexname;
