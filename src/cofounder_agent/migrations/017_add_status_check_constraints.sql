-- Migration: Add CHECK constraints for status/stage/approval_status/publish_mode
-- Fixes issue #260 — no CHECK constraints on enum-like columns; typos silently corrupt data.
-- Version: 017
-- NOTE: Run a data-cleanup pass first to normalise any out-of-range values:
--   SELECT DISTINCT status FROM content_tasks;
--   SELECT DISTINCT stage FROM content_tasks;
--   SELECT DISTINCT approval_status FROM content_tasks;
--   SELECT DISTINCT publish_mode FROM content_tasks;
--   SELECT DISTINCT old_status, new_status FROM task_status_history;
-- Correct any values outside the allowed sets before running this migration.

BEGIN;

ALTER TABLE content_tasks
  ADD CONSTRAINT chk_content_tasks_status
    CHECK (status IN ('pending', 'queued', 'in_progress', 'completed', 'failed', 'cancelled')),
  ADD CONSTRAINT chk_content_tasks_approval_status
    CHECK (approval_status IN ('pending', 'approved', 'rejected') OR approval_status IS NULL),
  ADD CONSTRAINT chk_content_tasks_publish_mode
    CHECK (publish_mode IN ('draft', 'published') OR publish_mode IS NULL),
  ADD CONSTRAINT chk_content_tasks_stage
    CHECK (stage IN ('pending', 'research', 'outline', 'draft', 'qa', 'image', 'publish', 'completed') OR stage IS NULL);

ALTER TABLE task_status_history
  ADD CONSTRAINT chk_status_history_old
    CHECK (old_status IN ('pending', 'queued', 'in_progress', 'completed', 'failed', 'cancelled') OR old_status IS NULL),
  ADD CONSTRAINT chk_status_history_new
    CHECK (new_status IN ('pending', 'queued', 'in_progress', 'completed', 'failed', 'cancelled'));

COMMIT;
