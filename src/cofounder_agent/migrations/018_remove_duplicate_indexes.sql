-- Migration: Remove duplicate indexes on content_tasks
-- Fixes issue #282 — ix_ prefixed indexes (SQLAlchemy-generated) duplicate the idx_ variants.
-- The idx_ hand-written variants are kept; the ix_ Alembic variants are dropped.
-- Version: 018
-- NOTE: Verify query plans with EXPLAIN before and after to confirm the idx_ variants are used.
--       Use CONCURRENTLY to avoid table lock in production.

-- Drop SQLAlchemy-generated duplicate indexes (keep the idx_ hand-written versions)
DROP INDEX CONCURRENTLY IF EXISTS ix_content_tasks_status;
DROP INDEX CONCURRENTLY IF EXISTS ix_content_tasks_task_id;
DROP INDEX CONCURRENTLY IF EXISTS ix_content_tasks_task_type;
DROP INDEX CONCURRENTLY IF EXISTS ix_content_tasks_created_at;
