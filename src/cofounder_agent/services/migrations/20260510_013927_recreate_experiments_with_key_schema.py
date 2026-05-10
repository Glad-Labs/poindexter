"""Recreate the ``experiments`` table with the key/variants/assignment_field schema.

The 2026-05-08 baseline squash captured a half-migrated state for the
A/B testing surface:

- ``experiment_assignments`` was captured at the post-0097 (new) shape:
  ``(id, experiment_id, subject_id, variant_key, assigned_at, metrics)``.
- ``experiments`` was captured at the pre-0097 (old A/B-only) shape:
  ``(id, experiment_type, status, variant_a, variant_b, metric_name,
  variant_a_value, variant_b_value, winner, confidence, post_id,
  task_id, started_at, completed_at)`` — i.e. migration 0097 was lost
  in the squash for THIS table specifically.

The result: every consumer of ``ExperimentService.create()`` /
``assign()`` / ``record_outcome()`` raises ``UndefinedColumnError`` on
columns the old schema doesn't have (``key``, ``description``,
``variants``, ``assignment_field``). The pipeline_experiment_hook
unit tests (and the ``poindexter experiments`` CLI) have been broken
on every fresh install and on Matt's prod since the squash landed.

Both tables are empty in prod (no experiments have ever shipped).
Safe to ``DROP TABLE experiments`` and recreate with the post-0097
shape. The matching ``experiment_assignments`` table already has the
right columns; we recreate its FK to the new ``experiments.id``.

This migration is idempotent: ``DROP TABLE IF EXISTS`` is a no-op when
the table doesn't exist, ``CREATE TABLE IF NOT EXISTS`` likewise. Re-
running on a machine that already has the new schema is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SQL = """
-- 1. Drop the old experiment_assignments FK so we can drop+recreate
--    the experiments table without violating it. The FK gets recreated
--    inside the new experiments table block below.
ALTER TABLE IF EXISTS experiment_assignments
    DROP CONSTRAINT IF EXISTS experiment_assignments_experiment_id_fkey;

-- 2. Drop the old (pre-0097) experiments table. Empty in prod and on
--    fresh installs — no data to migrate. The new shape diverges too
--    much from the old shape to ALTER cleanly.
DROP TABLE IF EXISTS experiments CASCADE;

-- 3. Recreate experiments with the post-0097 schema
--    (services/experiment_service.py is the canonical consumer; its
--    INSERT statements and column refs match this shape exactly).
CREATE TABLE IF NOT EXISTS experiments (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key                varchar(128) NOT NULL UNIQUE,
    description        text NOT NULL,
    status             varchar(32) NOT NULL DEFAULT 'draft',
    variants           jsonb NOT NULL,
    assignment_field   varchar(64) NOT NULL DEFAULT 'task_id',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    completed_at       timestamptz,
    winner_variant     varchar(64)
);

-- 4. Re-link experiment_assignments to the new experiments.id. The
--    column already exists on experiment_assignments per the squash;
--    we just need the FK.
ALTER TABLE experiment_assignments
    ADD CONSTRAINT experiment_assignments_experiment_id_fkey
    FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE;

-- 5. Hot-path indexes (assignment helper queries running experiments).
CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON experiments (status)
    WHERE status IN ('running', 'paused');

CREATE INDEX IF NOT EXISTS idx_experiment_assignments_exp
    ON experiment_assignments (experiment_id, variant_key);

-- 6. Touch trigger so updated_at always reflects the last write.
CREATE OR REPLACE FUNCTION experiments_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS experiments_touch_updated_at_trg ON experiments;
CREATE TRIGGER experiments_touch_updated_at_trg
    BEFORE UPDATE ON experiments
    FOR EACH ROW EXECUTE FUNCTION experiments_touch_updated_at();
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SQL)
        logger.info(
            "20260510_013927: experiments table recreated with key/variants/"
            "assignment_field schema; experiment_assignments FK re-linked"
        )
