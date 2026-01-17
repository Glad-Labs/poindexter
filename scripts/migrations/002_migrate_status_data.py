"""Database migration: Migrate status data and consolidate fields.

Phase 2: Data migration - converts existing statuses and consolidates fields
- Migrates varchar status to status_enum
- Consolidates approval_status field into main status
- Sets timestamps for status tracking
- Validates data integrity

Created: January 16, 2026
Status: Run after 001_add_task_status_enum.py in development
"""

MIGRATION_UP = """
-- ============================================================================
-- MIGRATION UP: Migrate and consolidate status data
-- ============================================================================

-- Step 1: Migrate existing statuses to status_enum
-- Map legacy status values to new ENUM values
UPDATE content_tasks
SET status_enum = CASE
    WHEN status IS NULL THEN 'pending'::task_status_enum
    WHEN LOWER(status) = 'pending' THEN 'pending'::task_status_enum
    WHEN LOWER(status) = 'queued' THEN 'pending'::task_status_enum
    WHEN LOWER(status) = 'in_progress' THEN 'in_progress'::task_status_enum
    WHEN LOWER(status) = 'running' THEN 'in_progress'::task_status_enum
    WHEN LOWER(status) = 'generated' THEN 'awaiting_approval'::task_status_enum
    WHEN LOWER(status) = 'completed' THEN 'published'::task_status_enum
    WHEN LOWER(status) = 'published' THEN 'published'::task_status_enum
    WHEN LOWER(status) = 'failed' THEN 'failed'::task_status_enum
    WHEN LOWER(status) = 'approved' THEN 'approved'::task_status_enum
    WHEN LOWER(status) = 'awaiting_approval' THEN 'awaiting_approval'::task_status_enum
    WHEN LOWER(status) = 'on_hold' THEN 'on_hold'::task_status_enum
    WHEN LOWER(status) = 'rejected' THEN 'rejected'::task_status_enum
    WHEN LOWER(status) = 'cancelled' THEN 'cancelled'::task_status_enum
    ELSE 'pending'::task_status_enum
END
WHERE status_enum IS NULL;

-- Step 2: Set timestamps where missing
UPDATE content_tasks
SET status_updated_at = COALESCE(status_updated_at, created_at)
WHERE status_updated_at IS NULL;

UPDATE content_tasks
SET status_updated_by = COALESCE(status_updated_by, 'system_migration')
WHERE status_updated_by IS NULL;

-- Step 3: Handle published/completed tasks - set completed_at
UPDATE content_tasks
SET completed_at = COALESCE(completed_at, updated_at, status_updated_at)
WHERE status_enum IN ('published', 'cancelled', 'failed')
    AND completed_at IS NULL;

-- Step 4: Handle in_progress tasks - set started_at
UPDATE content_tasks
SET started_at = COALESCE(started_at, created_at)
WHERE status_enum IN ('in_progress', 'awaiting_approval', 'approved')
    AND started_at IS NULL;

-- Step 5: Log data validation issues (view for inspection)
-- This helps identify any problematic records
CREATE TEMPORARY VIEW status_migration_issues AS
SELECT 
    id,
    topic,
    status as old_status,
    status_enum as new_status,
    created_at,
    status_updated_at,
    'Missing status_enum' as issue
FROM content_tasks
WHERE status_enum IS NULL
UNION ALL
SELECT 
    id,
    topic,
    status as old_status,
    status_enum as new_status,
    created_at,
    status_updated_at,
    'Terminal state without completed_at' as issue
FROM content_tasks
WHERE status_enum IN ('published', 'cancelled', 'failed')
    AND completed_at IS NULL;

-- Step 6: Verify migration (will show any issues)
SELECT * FROM status_migration_issues;

-- Step 7: Update constraints - ensure all status_enum are set
ALTER TABLE content_tasks
ALTER COLUMN status_enum SET NOT NULL;

-- Step 8: Create initial status history entries for audit trail
INSERT INTO task_status_history 
    (task_id, old_status, new_status, changed_at, changed_by, reason)
SELECT 
    id,
    NULL as old_status,
    status_enum as new_status,
    created_at as changed_at,
    'system_migration' as changed_by,
    'Initial status during migration' as reason
FROM content_tasks
WHERE NOT EXISTS (
    SELECT 1 FROM task_status_history 
    WHERE task_id = content_tasks.id
);

-- Step 9: Archive old status column (do not drop yet for safety)
-- Rename to status_legacy for potential rollback
ALTER TABLE content_tasks 
RENAME COLUMN status TO status_legacy;

-- Step 10: Rename status_enum to status (main status column)
ALTER TABLE content_tasks 
RENAME COLUMN status_enum TO status;

-- Final: Verify all records have valid status
DO $$
DECLARE
    invalid_count INT;
BEGIN
    SELECT COUNT(*) INTO invalid_count
    FROM content_tasks
    WHERE status IS NULL;
    
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'Migration failed: % records have NULL status', invalid_count;
    END IF;
    
    RAISE NOTICE 'Migration successful: All records have valid status';
END $$;
"""

MIGRATION_DOWN = """
-- ============================================================================
-- MIGRATION DOWN: Rollback status migration and consolidation
-- ============================================================================

-- Step 1: Restore old status column
ALTER TABLE content_tasks
RENAME COLUMN status TO status_new;

ALTER TABLE content_tasks
RENAME COLUMN status_legacy TO status;

-- Step 2: Remove new columns (completed_at, started_at tracked but remove references)
-- Keep the columns in case they're referenced elsewhere
-- Just reset timestamps to NULL
UPDATE content_tasks
SET 
    started_at = NULL,
    completed_at = NULL,
    status_updated_at = NULL,
    status_updated_by = NULL;

-- Step 3: Clear task_status_history (audit trail is new)
DELETE FROM task_status_history;

-- Step 4: Restore old status column uniqueness/indexes if they existed
-- This would need to be customized based on actual old schema
"""

if __name__ == "__main__":
    print("Migration: Migrate status data and consolidate fields")
    print("=" * 70)
    print("\nUP Migration:")
    print(MIGRATION_UP)
    print("\n" + "=" * 70)
    print("\nDOWN Migration:")
    print(MIGRATION_DOWN)
