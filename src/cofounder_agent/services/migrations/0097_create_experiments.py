"""Migration 0097: ``experiments`` + ``experiment_assignments`` tables.

Closes the gap Matt called out during overnight prod-hardening: the
codebase already has the building blocks for A/B testing
(``qa_workflow_*`` configs in app_settings, swappable
``pipeline_writer_model``, content scoring metrics) but no harness to
declare an experiment and route pipeline runs through it. This
migration is the storage layer for that harness.

Two tables:

- ``experiments`` — operator-declared A/B tests. ``variants`` is JSONB
  so any pipeline knob can be swapped per arm (writer model, prompt
  template, scoring weights, scene visuals strategy, image style,
  whatever). The ``status`` column lets the assignment helper short-
  circuit on draft/paused/complete experiments without per-row config
  fetches.

- ``experiment_assignments`` — sticky, per-subject decisions.
  ``UNIQUE(experiment_id, subject_id)`` enforces the "same task always
  gets the same arm" property so the harness is deterministic across
  worker restarts. ``metrics`` is JSONB and merged in (``||``) on each
  ``record_outcome`` so the runtime can attribute multiple downstream
  signals (initial QA score, post-publish CTR, revenue, etc.) to the
  same assignment row.

Migration is idempotent — uses ``CREATE TABLE IF NOT EXISTS`` and
``CREATE INDEX IF NOT EXISTS`` everywhere. ``down()`` drops indexes
then tables in reverse order (assignments first because of the FK).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS experiments (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable slug — what callers reference at assignment time
    -- (``ExperimentService.assign(experiment_key="writer_model_2026q2",
    -- ...)``). Globally unique so a key resolves to exactly one row.
    key                varchar(128) NOT NULL UNIQUE,
    -- Human description for the operator UI / dashboards.
    description        text NOT NULL,
    -- Lifecycle. Only ``running`` experiments produce assignments.
    -- ``paused`` keeps existing assignments observable but stops new
    -- ones; ``complete`` freezes everything and is the operator-set
    -- terminal state along with ``winner_variant``.
    status             varchar(32) NOT NULL DEFAULT 'draft',
    -- Variant array — JSONB list of {key, weight, config} entries.
    -- ``config`` is opaque to the harness; the calling Stage interprets
    -- it (e.g. {"writer_model": "glm-4.7-5090"}). Validation lives in
    -- ExperimentService.create() (≥2 variants, weights sum to ≈100,
    -- each variant has key/weight/config).
    variants           jsonb NOT NULL,
    -- Which context field to hash for sticky assignment. Defaults to
    -- ``task_id`` because that's the most natural "subject" for the
    -- content pipeline; experiments at other layers can override
    -- (e.g. ``post_id`` for a publishing-time experiment, ``user_id``
    -- for a UI experiment if/when we ship one).
    assignment_field   varchar(64) NOT NULL DEFAULT 'task_id',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    -- Set when status flips draft→running.
    started_at         timestamptz,
    -- Set by ``conclude()`` along with ``winner_variant``.
    completed_at       timestamptz,
    -- Operator-chosen winner. Promoting the winning config into
    -- production app_settings is a deliberate manual step — see
    -- ExperimentService.conclude() docstring.
    winner_variant     varchar(64)
);

CREATE TABLE IF NOT EXISTS experiment_assignments (
    id                 bigserial PRIMARY KEY,
    experiment_id      uuid NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    -- The hashed subject (task_id, post_id, user_id...). varchar so
    -- we don't have to know the underlying type at schema time.
    subject_id         varchar(128) NOT NULL,
    -- Which arm the subject was assigned to. Matches one of the
    -- ``variants[*].key`` values in the parent experiment row.
    variant_key        varchar(64) NOT NULL,
    assigned_at        timestamptz NOT NULL DEFAULT now(),
    -- Per-assignment outcome: scores, durations, downstream signals.
    -- Merged in (``||``) on each ``record_outcome`` call so multiple
    -- pipeline phases can attribute to the same row.
    metrics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Sticky-assignment guarantee: same subject in the same experiment
    -- always lands on the same row. ON CONFLICT DO NOTHING in the
    -- service relies on this constraint.
    UNIQUE (experiment_id, subject_id)
);

-- Status filter — assignment helper queries running experiments on the
-- hot path, so the partial index keeps the read O(running_count)
-- rather than O(total_experiments).
CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON experiments (status)
    WHERE status IN ('running', 'paused');

-- Summary queries group by (experiment, variant). Composite index
-- gives the planner a covering path for the per-variant aggregation.
CREATE INDEX IF NOT EXISTS idx_experiment_assignments_exp
    ON experiment_assignments (experiment_id, variant_key);

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


SQL_DOWN = """
DROP TRIGGER IF EXISTS experiments_touch_updated_at_trg ON experiments;
DROP FUNCTION IF EXISTS experiments_touch_updated_at();
DROP INDEX IF EXISTS idx_experiment_assignments_exp;
DROP INDEX IF EXISTS idx_experiments_status;
DROP TABLE IF EXISTS experiment_assignments;
DROP TABLE IF EXISTS experiments;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        logger.info("0097: created experiments + experiment_assignments tables")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0097: dropped experiments + experiment_assignments tables")
