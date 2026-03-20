-- Rollback: Consolidate to Single Tasks Table
-- Reverses: 007_consolidate_to_single_tasks_table.sql
-- NOTE: This migration only dropped a foreign key constraint.
-- Re-adding the FK is only safe if the data still satisfies the constraint.
-- Verify data integrity before applying this rollback.

BEGIN;

-- Re-add the foreign key constraint that was dropped by migration 007
-- Only safe if content_tasks.task_id values all exist in tasks.task_id
ALTER TABLE content_tasks
    ADD CONSTRAINT content_tasks_task_id_fkey
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
    ON DELETE CASCADE;

COMMIT;
