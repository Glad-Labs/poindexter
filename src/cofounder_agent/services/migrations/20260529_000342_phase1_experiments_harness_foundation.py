"""Migration 20260529_000342: Phase 1 — variant experiments harness foundation.

PR 1 of 5 for the content R&D lab's Phase 1 (per design doc
``docs/architecture/2026-05-28-phase-1-variant-experiments-design.md``,
committed at PR #698). This migration ships the **schema foundation only**
— no runner, no CLI, no Grafana wiring. Those land in PRs 2-5.

What this delivers:

1. ``experiments`` — one row per named test (e.g.
   ``glad-labs/writer-model-gemma4-vs-qwen36-2026-05``) scoped to a
   single ``niche_slug``. Holds the per-experiment objective function +
   auto-pause + cost-guard thresholds the Phase 2 runner reads.

2. ``experiment_variants`` — the 2-N concrete configurations the writer
   atom samples from for an experiment. ``active=true`` rows are
   eligible; PR 2 flips ``active=false`` and stamps ``paused_at`` /
   ``paused_reason`` when the auto-pause gate trips.

3. ``capability_outcomes.variant_id`` — telemetry FK so every atom row
   the pipeline writes knows which variant produced it. ``ON DELETE
   SET NULL`` (not CASCADE) so cleaning up a variant doesn't nuke the
   historical outcome rows that prove the experiment ran — Phase 0
   already pays the storage cost of keeping those rows; we keep them.

4. ``lab_outcomes_v1`` (extended) — adds the variant + experiment
   context columns so every downstream consumer (Grafana, the
   learnings digest) sees the experiment context inline without an
   extra join.

5. ``experiment_variant_scorecard_v1`` — the per-variant rollup the
   CLI (PR 3) and Grafana panels (PR 4) read. Aggregates posts
   attempted / approved, approval rate, edit distance, views 24h /
   7d, cost. The Phase 2 bandit reads this view directly once it lands.

Scientific-method control: the design locks in one-axis-varying per
experiment + one active experiment per niche (enforced via a partial
unique index here so the second ``UPDATE ... SET status='active'``
fails with a clean UNIQUE violation instead of silently producing two
actives). Per ``feedback_db_first_config`` the auto-pause + cost-guard
thresholds (``min_approval_rate_pct``, ``min_posts_before_pause``,
``cost_alert_multiplier``) live as columns on the experiments row —
not as Python constants — so an operator can tune them per-experiment
without a code change.

Per ``feedback_backcompat_now_required`` (2026-05-27 forward):

- Every DDL is ``IF NOT EXISTS`` / ``CREATE OR REPLACE``; the
  migration is safe to re-run.
- The single column added to an existing table (``variant_id`` on
  ``capability_outcomes``) is **nullable** with no default, so every
  existing writer continues to land rows unchanged. Historical rows
  see NULL in the new columns of ``lab_outcomes_v1``.
- ``down()`` fully reverses ``up()``: drops the scorecard view, restores
  ``lab_outcomes_v1`` to its Phase 0 shape (copied verbatim from
  ``20260528_204250_lab_observability_columns_and_view.py``), drops
  the FK column + its partial index, drops both new tables.

Verification once applied:

    docker exec poindexter-postgres-local psql -U poindexter \\
      -d poindexter_brain -c '\\d+ experiments'
    docker exec poindexter-postgres-local psql -U poindexter \\
      -d poindexter_brain -c '\\d+ experiment_variants'
    docker exec poindexter-postgres-local psql -U poindexter \\
      -d poindexter_brain -c '\\d+ experiment_variant_scorecard_v1'
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# experiments — one row per named test, scoped to a single niche.
#
# The CHECK on objective_function intentionally enumerates only the
# rollups the scorecard view knows how to compute today; adding a new
# objective is a follow-up migration that extends both the CHECK and
# the view in lockstep. Operators can read this list with
#   SELECT pg_get_constraintdef(oid) FROM pg_constraint
#   WHERE conname LIKE '%objective_function%';
# ---------------------------------------------------------------------------
_EXPERIMENTS_DDL = """\
CREATE TABLE IF NOT EXISTS experiments (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key                     text NOT NULL UNIQUE,
  niche_slug              text NOT NULL,
  description             text NOT NULL DEFAULT '',
  status                  text NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft','active','paused','concluded')),
  -- The metric the scorecard ranks variants by. Default 'views_7d' per
  -- the design doc's three-tier reward stack (views primary). Configurable
  -- per experiment so future experiments can rank on 'revenue_per_post'
  -- etc. once those columns reach the scorecard view.
  objective_function      text NOT NULL DEFAULT 'views_7d'
                              CHECK (objective_function IN (
                                'views_7d','views_24h','approval_rate',
                                'views_per_dollar','composite_score'
                              )),
  -- Per-experiment auto-pause + cost-guard thresholds. Defaults match
  -- the design doc's "testing in production" posture. Per
  -- feedback_db_first_config these live as columns, not Python constants,
  -- so operators can tune them per-experiment without code changes.
  min_approval_rate_pct   integer NOT NULL DEFAULT 50,
  min_posts_before_pause  integer NOT NULL DEFAULT 10,
  cost_alert_multiplier   numeric NOT NULL DEFAULT 3.0,
  created_at              timestamptz NOT NULL DEFAULT now(),
  activated_at            timestamptz,
  concluded_at            timestamptz,
  conclusion_note         text
)
"""

# One active experiment per niche — enforced via a partial unique index
# so a deliberate draft→active UPDATE on a second experiment fails with
# a clean UNIQUE violation. The Phase 0 design doc allowed a duplicate
# index without uniqueness; we strengthen that here to match the design
# doc's "one active experiment per niche" constraint (referenced in the
# task spec).
_EXPERIMENTS_PARTIAL_UNIQUE_DDL = """\
CREATE UNIQUE INDEX IF NOT EXISTS idx_experiments_one_active_per_niche
  ON experiments(niche_slug) WHERE status = 'active'
"""

_EXPERIMENTS_KEY_INDEX_DDL = """\
CREATE INDEX IF NOT EXISTS idx_experiments_key ON experiments(key)
"""


# ---------------------------------------------------------------------------
# experiment_variants — the 2-N concrete configurations.
#
# NULL override columns mean "inherit the niche production default for
# that axis" — keeps the scientific-method control posture (only the
# axis the experiment is testing varies; everything else is held).
# Phase 2's runner reads these NULLs and falls back to the niche
# defaults from `niches` rows.
# ---------------------------------------------------------------------------
_EXPERIMENT_VARIANTS_DDL = """\
CREATE TABLE IF NOT EXISTS experiment_variants (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id            uuid NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  label                    text NOT NULL,
  weight                   numeric NOT NULL DEFAULT 1.0,
  -- Override knobs. NULL = inherit the niche production default for
  -- that axis. Phase 1 starts with prompt-version variants only; the
  -- writer_model + rag_config columns are present so PR 2's runner
  -- doesn't need a follow-up migration to support multi-axis tests.
  prompt_template_key      text,
  prompt_template_version  integer,
  writer_model             text,
  rag_config               jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Auto-pause state: PR 2's gate flips active=false + stamps
  -- paused_at/paused_reason when approval_rate < experiments.min_approval_rate_pct
  -- after experiments.min_posts_before_pause samples. The runner reads
  -- the active=true rows only.
  active                   boolean NOT NULL DEFAULT true,
  paused_at                timestamptz,
  paused_reason            text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  UNIQUE (experiment_id, label)
)
"""

_EXPERIMENT_VARIANTS_ACTIVE_INDEX_DDL = """\
CREATE INDEX IF NOT EXISTS idx_experiment_variants_active_lookup
  ON experiment_variants(experiment_id) WHERE active
"""


# ---------------------------------------------------------------------------
# capability_outcomes.variant_id — the telemetry FK.
#
# ON DELETE SET NULL (not CASCADE) so cleanup of an experiment_variant
# row doesn't nuke historical outcome rows. We want the rows for
# posterity even after the variant row is gone — the run happened, the
# atom emitted, the post (if any) was approved or rejected, and the
# scorecard for posterity should still be able to count it (rolled up
# into the "unattributed" bucket once the variant_id is NULL).
# ---------------------------------------------------------------------------
_CAPABILITY_OUTCOMES_ADD_VARIANT_ID_DDL = """\
ALTER TABLE capability_outcomes
  ADD COLUMN IF NOT EXISTS variant_id uuid
    REFERENCES experiment_variants(id) ON DELETE SET NULL
"""

_CAPABILITY_OUTCOMES_VARIANT_INDEX_DDL = """\
CREATE INDEX IF NOT EXISTS idx_capability_outcomes_variant
  ON capability_outcomes(variant_id) WHERE variant_id IS NOT NULL
"""


# ---------------------------------------------------------------------------
# lab_outcomes_v1 — extended with variant + experiment context.
#
# The original Phase 0 definition is preserved column-for-column from
# 20260528_204250_lab_observability_columns_and_view.py (the source of
# truth for the v1 shape). The new variant_label / variant_id /
# experiment_key / experiment_status / experiment_objective_function
# columns append to the end of the SELECT list per the task spec, so
# every existing consumer keeps reading the same prefix unchanged.
#
# Two new LEFT JOINs add the experiment context. Both are LEFT JOINs —
# rows for tasks that ran outside any experiment (the historical 1664
# rows + any future production-only runs) keep their existing
# columns and get NULL in the new ones.
#
# WHERE / 90-day window preserved exactly. No ORDER BY in the original
# definition (views don't carry one); the existing shape is faithful.
# ---------------------------------------------------------------------------
_LAB_OUTCOMES_V1_PHASE1_SQL = """\
CREATE OR REPLACE VIEW lab_outcomes_v1 AS
SELECT
  co.task_id,
  co.niche_slug,
  co.template_slug,
  co.atom_name,
  co.model_used,
  co.prompt_template_key,
  co.prompt_template_version,
  co.ok            AS atom_ok,
  co.halted        AS atom_halted,
  co.quality_score AS atom_quality_score,
  co.elapsed_ms,
  co.created_at    AS run_at,
  ro.actual_cost,
  ro.estimated_cost,
  ro.compute_tier,
  ro.success       AS routing_success,
  pem.approver,
  pem.char_diff_count,
  pem.line_diff_count,
  pem.pre_approve_len,
  pem.post_approve_len,
  pem.approve_method,
  pem.approved_at,
  pv_count.views_24h AS views_24h_post_publish,
  pv_count.views_7d  AS views_7d_post_publish,
  ev.label   AS variant_label,
  ev.id      AS variant_id,
  e.key      AS experiment_key,
  e.status   AS experiment_status,
  e.objective_function AS experiment_objective_function
FROM capability_outcomes co
LEFT JOIN routing_outcomes ro
  ON ro.task_id = co.task_id
LEFT JOIN published_post_edit_metrics pem
  ON pem.task_id = co.task_id
LEFT JOIN LATERAL (
  SELECT
    COUNT(*) FILTER (
      WHERE pv.created_at BETWEEN pem.approved_at
        AND pem.approved_at + INTERVAL '24 hours'
    ) AS views_24h,
    COUNT(*) FILTER (
      WHERE pv.created_at BETWEEN pem.approved_at
        AND pem.approved_at + INTERVAL '7 days'
    ) AS views_7d
  FROM page_views pv
  JOIN posts p ON p.slug = pv.slug
  WHERE pem.approved_at IS NOT NULL
    AND p.metadata ->> 'pipeline_task_id' = co.task_id
) pv_count ON TRUE
LEFT JOIN experiment_variants ev ON ev.id = co.variant_id
LEFT JOIN experiments e ON e.id = ev.experiment_id
WHERE co.created_at > NOW() - INTERVAL '90 days'
"""


# ---------------------------------------------------------------------------
# experiment_variant_scorecard_v1 — per-variant rollup.
#
# Read by PR 3 (poindexter experiments status CLI) and PR 4 (Grafana
# panels). Returns one row per (experiment, variant) pair so a variant
# with no outcome rows yet (newly added, never sampled) still shows up
# with posts_attempted=0 — the operator sees that the variant exists
# but hasn't run.
#
# The objective_function column tells the consumer which metric to
# rank on; Phase 1 ranking is manual, Phase 2's bandit reads this view
# directly. Aggregations match the design doc's three-tier reward
# stack: views (primary), approval rate (secondary safety gate), cost
# (tiebreaker).
# ---------------------------------------------------------------------------
_SCORECARD_DDL = """\
CREATE OR REPLACE VIEW experiment_variant_scorecard_v1 AS
SELECT
  e.id              AS experiment_id,
  e.key             AS experiment_key,
  e.niche_slug,
  e.status          AS experiment_status,
  e.objective_function,
  ev.id             AS variant_id,
  ev.label          AS variant_label,
  ev.weight,
  ev.active         AS variant_active,
  ev.paused_at,
  ev.paused_reason,
  COUNT(DISTINCT lo.task_id)                                                AS posts_attempted,
  COUNT(DISTINCT lo.task_id) FILTER (WHERE lo.approver IS NOT NULL)         AS posts_approved,
  ROUND(
    COUNT(DISTINCT lo.task_id) FILTER (WHERE lo.approver IS NOT NULL)::numeric
    / NULLIF(COUNT(DISTINCT lo.task_id), 0) * 100, 1
  )                                                                          AS approval_rate_pct,
  AVG(lo.char_diff_count::numeric / NULLIF(lo.pre_approve_len, 0))
    FILTER (WHERE lo.approver IS NOT NULL)                                   AS avg_edit_distance_pct,
  AVG(lo.views_24h_post_publish)                                             AS avg_views_24h,
  AVG(lo.views_7d_post_publish)                                              AS avg_views_7d,
  AVG(lo.actual_cost)                                                        AS avg_cost_per_post,
  SUM(lo.actual_cost)                                                        AS total_cost
FROM experiments e
JOIN experiment_variants ev ON ev.experiment_id = e.id
LEFT JOIN lab_outcomes_v1 lo ON lo.variant_id = ev.id
GROUP BY
  e.id, e.key, e.niche_slug, e.status, e.objective_function,
  ev.id, ev.label, ev.weight, ev.active, ev.paused_at, ev.paused_reason
"""

_SCORECARD_COMMENT_SQL = (
    "COMMENT ON VIEW experiment_variant_scorecard_v1 IS "
    "'Per-variant rollup for active+concluded experiments. Read by the "
    "poindexter experiments status CLI (PR 3) and the Grafana panels "
    "(PR 4). objective_function tells the consumer which column to rank "
    "on. Phase 1 ranks manually; Phase 2 bandit reads this directly.'"
)


# ---------------------------------------------------------------------------
# Phase 0 lab_outcomes_v1 — verbatim copy for down() restoration.
#
# Copied from 20260528_204250_lab_observability_columns_and_view.py so
# down() restores the view to exactly the shape the Phase 0 migration
# left it in (no variant/experiment columns, no new joins). If that
# file ever changes its definition, this string must change in lockstep
# — but Phase 0 is the v1 contract floor, so it should not change
# (per the v1 suffix discipline documented in the Phase 0 migration).
# ---------------------------------------------------------------------------
_LAB_OUTCOMES_V1_PHASE0_SQL = """\
CREATE OR REPLACE VIEW lab_outcomes_v1 AS
SELECT
  co.task_id,
  co.niche_slug,
  co.template_slug,
  co.atom_name,
  co.model_used,
  co.prompt_template_key,
  co.prompt_template_version,
  co.ok            AS atom_ok,
  co.halted        AS atom_halted,
  co.quality_score AS atom_quality_score,
  co.elapsed_ms,
  co.created_at    AS run_at,
  ro.actual_cost,
  ro.estimated_cost,
  ro.compute_tier,
  ro.success       AS routing_success,
  pem.approver,
  pem.char_diff_count,
  pem.line_diff_count,
  pem.pre_approve_len,
  pem.post_approve_len,
  pem.approve_method,
  pem.approved_at,
  pv_count.views_24h AS views_24h_post_publish,
  pv_count.views_7d  AS views_7d_post_publish
FROM capability_outcomes co
LEFT JOIN routing_outcomes ro
  ON ro.task_id = co.task_id
LEFT JOIN published_post_edit_metrics pem
  ON pem.task_id = co.task_id
LEFT JOIN LATERAL (
  SELECT
    COUNT(*) FILTER (
      WHERE pv.created_at BETWEEN pem.approved_at
        AND pem.approved_at + INTERVAL '24 hours'
    ) AS views_24h,
    COUNT(*) FILTER (
      WHERE pv.created_at BETWEEN pem.approved_at
        AND pem.approved_at + INTERVAL '7 days'
    ) AS views_7d
  FROM page_views pv
  JOIN posts p ON p.slug = pv.slug
  WHERE pem.approved_at IS NOT NULL
    AND p.metadata ->> 'pipeline_task_id' = co.task_id
) pv_count ON TRUE
WHERE co.created_at > NOW() - INTERVAL '90 days'
"""


async def up(pool) -> None:
    """Apply the migration.

    Order matters:

    1. Create ``experiments`` first (referenced by experiment_variants FK).
    2. Create ``experiment_variants`` (referenced by capability_outcomes FK).
    3. Add ``variant_id`` to ``capability_outcomes`` (FK target now exists).
    4. Replace ``lab_outcomes_v1`` (depends on the new variant_id column
       + the experiments / experiment_variants tables).
    5. Create the scorecard view (depends on the freshly-extended
       lab_outcomes_v1).

    All DDL is IF NOT EXISTS / OR REPLACE so a replay against a
    partially-applied DB is a no-op.
    """
    async with pool.acquire() as conn:
        # 1. experiments table + indexes
        await conn.execute(_EXPERIMENTS_DDL)
        await conn.execute(_EXPERIMENTS_PARTIAL_UNIQUE_DDL)
        await conn.execute(_EXPERIMENTS_KEY_INDEX_DDL)

        # 2. experiment_variants table + active-lookup index
        await conn.execute(_EXPERIMENT_VARIANTS_DDL)
        await conn.execute(_EXPERIMENT_VARIANTS_ACTIVE_INDEX_DDL)

        # 3. capability_outcomes.variant_id + partial index
        await conn.execute(_CAPABILITY_OUTCOMES_ADD_VARIANT_ID_DDL)
        await conn.execute(_CAPABILITY_OUTCOMES_VARIANT_INDEX_DDL)

        # 4. lab_outcomes_v1 extended with variant + experiment columns
        await conn.execute(_LAB_OUTCOMES_V1_PHASE1_SQL)

        # 5. experiment_variant_scorecard_v1 + descriptive comment
        await conn.execute(_SCORECARD_DDL)
        await conn.execute(_SCORECARD_COMMENT_SQL)

    logger.info(
        "[migration] phase1_experiments_harness_foundation: applied — "
        "experiments + experiment_variants + capability_outcomes.variant_id + "
        "extended lab_outcomes_v1 + experiment_variant_scorecard_v1",
    )


async def down(pool) -> None:
    """Revert the migration.

    Reverse order of up():

    1. Drop the scorecard view (depends on lab_outcomes_v1 which we're
       about to restore).
    2. Restore ``lab_outcomes_v1`` to the Phase 0 definition (drops the
       experiment/variant joins so the FK + tables can be dropped).
    3. Drop the variant_id index + column on capability_outcomes.
    4. Drop ``experiment_variants`` (cascades drop the FK from
       capability_outcomes if step 3 missed any rows).
    5. Drop ``experiments``.

    All operations are IF EXISTS so re-running down() against an
    already-down DB is safe. Per ``feedback_finish_migrations`` both
    directions actually do the work — no half-down state.
    """
    async with pool.acquire() as conn:
        # 1. Scorecard view
        await conn.execute("DROP VIEW IF EXISTS experiment_variant_scorecard_v1")

        # 2. Restore lab_outcomes_v1 to the Phase 0 shape so the
        #    variant_id column + experiments tables can drop without
        #    a "view depends on column" error.
        #
        #    CREATE OR REPLACE VIEW cannot remove columns from the
        #    SELECT list (PostgreSQL rejects "cannot drop columns from
        #    view" because clients may have prepared statements bound
        #    to the wider shape). DROP + recreate is the documented
        #    workaround for any width-narrowing replacement.
        await conn.execute("DROP VIEW IF EXISTS lab_outcomes_v1")
        await conn.execute(_LAB_OUTCOMES_V1_PHASE0_SQL)

        # 3. variant_id column on capability_outcomes (+ its partial index)
        await conn.execute("DROP INDEX IF EXISTS idx_capability_outcomes_variant")
        await conn.execute(
            "ALTER TABLE capability_outcomes DROP COLUMN IF EXISTS variant_id"
        )

        # 4. experiment_variants — CASCADE not needed because step 3
        #    removed the FK column, but kept on the table drop itself
        #    for defensive idempotency if a partial up() left a
        #    dependent object behind.
        await conn.execute("DROP INDEX IF EXISTS idx_experiment_variants_active_lookup")
        await conn.execute("DROP TABLE IF EXISTS experiment_variants CASCADE")

        # 5. experiments
        await conn.execute("DROP INDEX IF EXISTS idx_experiments_one_active_per_niche")
        await conn.execute("DROP INDEX IF EXISTS idx_experiments_key")
        await conn.execute("DROP TABLE IF EXISTS experiments CASCADE")

    logger.info(
        "[migration] phase1_experiments_harness_foundation down: "
        "scorecard + lab_outcomes_v1 restored to Phase 0 + variant_id "
        "+ experiment_variants + experiments dropped",
    )
