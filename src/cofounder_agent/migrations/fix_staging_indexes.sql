-- ============================================================================
-- FIX DUPLICATE INDEX ERROR
-- ============================================================================
-- PostgreSQL Duplicate Index Fix for Staging Environment
-- Error: DuplicateTableError: relation "idx_timestamp_desc" already exists
-- 
-- This script removes the old index so SQLAlchemy can create the correct ones
-- Safe to run: All operations use IF EXISTS to prevent errors
-- ============================================================================

-- Step 1: Drop old duplicate indexes (non-critical, PostgreSQL will handle missing ones)
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

-- Step 2: List all indexes (to verify they were dropped)
SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename IN ('logs', 'tasks', 'audit_logs')
ORDER BY tablename, indexname;

-- Step 3: Verify logs table structure
\d logs

-- ============================================================================
-- AFTER RUNNING THIS SCRIPT:
-- ============================================================================
-- 1. Restart the Co-Founder Agent service in Railway:
--    Dashboard → Co-Founder Agent → Deployments → Click latest → Redeploy
--
-- 2. Wait 2-3 minutes for restart
--
-- 3. Test health endpoint:
--    curl https://your-staging-api.railway.app/api/health
--
-- 4. Expected response:
--    {"status": "healthy", "timestamp": "...", "version": "1.0.0"}
--
-- ============================================================================
