"""Database migration: Add task status ENUM and audit infrastructure.

Phase 1: Non-breaking changes - adds new columns and types
- Creates task_status_enum type
- Adds status tracking columns
- Creates audit table
- Data migration happens in Phase 2

Created: January 16, 2026
Status: Ready for deployment to development environment
"""

MIGRATION_UP = """
-- ============================================================================
-- MIGRATION UP: Add enterprise-level task status tracking
-- ============================================================================

-- Step 1: Create ENUM type for task statuses
CREATE TYPE task_status_enum AS ENUM (
    'pending',
    'in_progress',
    'awaiting_approval',
    'approved',
    'published',
    'failed',
    'on_hold',
    'rejected',
    'cancelled'
);

-- Step 2: Add new columns to content_tasks (non-breaking)
-- These columns will be populated in Phase 2
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS status_enum task_status_enum,
ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS status_updated_by VARCHAR(255),
ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE;

-- Step 3: Create audit table for status history
CREATE TABLE IF NOT EXISTS task_status_history (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    old_status task_status_enum,
    new_status task_status_enum NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    changed_by VARCHAR(255),
    reason VARCHAR(500),
    metadata JSONB,
    CONSTRAINT fk_task_status_history_task_id
        FOREIGN KEY (task_id) REFERENCES content_tasks(id) ON DELETE CASCADE
);

-- Step 4: Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_task_status_history_task_id 
    ON task_status_history(task_id);

CREATE INDEX IF NOT EXISTS idx_task_status_history_changed_at 
    ON task_status_history(changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_content_tasks_status_enum 
    ON content_tasks(status_enum);

CREATE INDEX IF NOT EXISTS idx_content_tasks_status_updated_at 
    ON content_tasks(status_updated_at DESC);

-- Step 5: Create view for active tasks
CREATE OR REPLACE VIEW active_tasks AS
SELECT 
    id,
    topic,
    task_name,
    status_enum as status,
    created_at,
    started_at,
    status_updated_at,
    status_updated_by,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - started_at)) / 60 as duration_minutes
FROM content_tasks
WHERE status_enum IN ('pending', 'in_progress', 'on_hold')
ORDER BY created_at DESC;

-- Step 6: Create view for recently completed tasks
CREATE OR REPLACE VIEW recently_completed_tasks AS
SELECT 
    id,
    topic,
    task_name,
    status_enum as status,
    completed_at,
    EXTRACT(EPOCH FROM (completed_at - created_at)) / 60 as total_duration_minutes
FROM content_tasks
WHERE status_enum IN ('published', 'cancelled')
    AND completed_at IS NOT NULL
ORDER BY completed_at DESC
LIMIT 100;
"""

MIGRATION_DOWN = """
-- ============================================================================
-- MIGRATION DOWN: Rollback task status changes
-- ============================================================================

-- Drop views
DROP VIEW IF EXISTS recently_completed_tasks;
DROP VIEW IF EXISTS active_tasks;

-- Drop indexes
DROP INDEX IF EXISTS idx_content_tasks_status_updated_at;
DROP INDEX IF EXISTS idx_content_tasks_status_enum;
DROP INDEX IF EXISTS idx_task_status_history_changed_at;
DROP INDEX IF EXISTS idx_task_status_history_task_id;

-- Drop audit table
DROP TABLE IF EXISTS task_status_history;

-- Drop new columns from content_tasks
ALTER TABLE content_tasks
DROP COLUMN IF EXISTS completed_at,
DROP COLUMN IF EXISTS started_at,
DROP COLUMN IF EXISTS status_updated_by,
DROP COLUMN IF EXISTS status_updated_at,
DROP COLUMN IF EXISTS status_enum;

-- Drop ENUM type
DROP TYPE IF EXISTS task_status_enum;
"""

if __name__ == "__main__":
    print("Migration: Add enterprise-level task status tracking")
    print("=" * 70)
    print("\nUP Migration:")
    print(MIGRATION_UP)
    print("\n" + "=" * 70)
    print("\nDOWN Migration:")
    print(MIGRATION_DOWN)
