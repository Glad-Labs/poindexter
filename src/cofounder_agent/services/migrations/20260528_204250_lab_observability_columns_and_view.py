"""Migration 20260528_204250: lab observability columns + lab_outcomes_v1 view.

Phase 0 of the content R&D lab. **Observation-only** — no optimisation,
no bandit, no auto-promotion. This migration establishes the foundation
every later phase reads from: a unified ``lab_outcomes_v1`` view that
joins ``capability_outcomes`` + ``routing_outcomes`` +
``published_post_edit_metrics`` + ``page_views`` per task_id, plus the
three observation columns each writer table was missing.

Why these specific columns:

- ``niche_slug`` — the durable routing seam (post-``writer_rag_mode``
  retirement, 2026-05-28). Every learnings digest / dashboard wants to
  slice outcomes per niche; without this column on
  ``capability_outcomes`` + ``routing_outcomes`` the join has to walk
  through ``pipeline_tasks`` for every row.
- ``prompt_template_key`` + ``prompt_template_version`` —
  ``UnifiedPromptManager`` resolves these at every call site. Stamping
  them on the outcome row gives the future variant-experiment phase
  the ability to attribute a quality_score drift to a specific prompt
  revision without a separate Langfuse correlation join.

All columns are **additive + nullable** so existing writers keep
working unchanged. ``record_run`` (capability_outcomes writer) reads
them best-effort from per-record metrics or state — old call paths
that don't stamp them produce rows with NULLs, which join cleanly.

The ``lab_outcomes_v1`` view is the single read surface for the lab.
Phase 1+ (variant experiments, bandit, learnings digest) all query
this view exclusively; no caller should touch the underlying tables
once this lands. The `_v1` suffix is the contract — the next
breaking schema change ships as ``lab_outcomes_v2`` alongside this
view (keep ``v1`` until consumers migrate, per
``feedback_backcompat_now_required``).

``views_24h_post_publish`` / ``views_7d_post_publish`` are computed
via a LATERAL join through ``page_views``. The page_views beacon has
been broken since 2026-04-09 (separate PR, task #269); these
columns will return 0 until that fix lands. The view is still useful
without them — every other field flows from working writers.

Verification once applied:

    \\d capability_outcomes
    \\d routing_outcomes
    \\d published_post_edit_metrics
    \\d+ lab_outcomes_v1
    SELECT atom_name, COUNT(*) FROM lab_outcomes_v1
     GROUP BY atom_name ORDER BY 2 DESC LIMIT 10;

Per ``feedback_backcompat_now_required`` (2026-05-27 forward): every
column is nullable, every operation is ``IF NOT EXISTS`` / ``OR
REPLACE``, and the down() path drops only what up() added (not the
underlying tables).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Split out for readability — the view DDL is the load-bearing chunk
# and reviewers should be able to scan it without scrolling past
# ALTER TABLE noise.
_VIEW_SQL = """\
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
  -- Correlate page_views → posts via slug, then posts → task via the
  -- canonical metadata->>'pipeline_task_id' seam (PR #693 / migration
  -- 20260528_021920). The legacy ``pem.post_id`` column is BIGINT and
  -- never populated by publish_service (posts.id is UUID); the task
  -- seam is what actually links the chain.
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
    """Apply the migration. All DDL is IF NOT EXISTS / OR REPLACE so a
    replay against a partially-applied DB is a no-op."""
    async with pool.acquire() as conn:
        # capability_outcomes — niche_slug + prompt template observability
        await conn.execute(
            """
            ALTER TABLE capability_outcomes
              ADD COLUMN IF NOT EXISTS niche_slug              text,
              ADD COLUMN IF NOT EXISTS prompt_template_key     text,
              ADD COLUMN IF NOT EXISTS prompt_template_version integer
            """
        )
        # Partial index — only rows with a niche_slug benefit from the
        # learnings-digest aggregations, which always filter on
        # niche_slug. Keeps the index small (today: ~10% of rows have
        # a niche_slug; the rest are manual / dev_diary infra runs).
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
              idx_capability_outcomes_niche_template
              ON capability_outcomes
                 (niche_slug, prompt_template_key, created_at DESC)
              WHERE niche_slug IS NOT NULL
            """
        )

        # routing_outcomes — same trio, but the existing columns use
        # varchar (not text) so we match that style here for diff
        # consistency on \d output.
        await conn.execute(
            """
            ALTER TABLE routing_outcomes
              ADD COLUMN IF NOT EXISTS niche_slug              varchar(100),
              ADD COLUMN IF NOT EXISTS prompt_template_key     varchar(200),
              ADD COLUMN IF NOT EXISTS prompt_template_version integer
            """
        )

        # published_post_edit_metrics — at approve time we look up the
        # most recent capability_outcomes row for this task and copy
        # these three fields onto the edit metric, so the lab view can
        # see "the operator edited N chars on the post that the writer
        # produced with prompt_template_key=X version=Y on model Z".
        await conn.execute(
            """
            ALTER TABLE published_post_edit_metrics
              ADD COLUMN IF NOT EXISTS model_used              text,
              ADD COLUMN IF NOT EXISTS prompt_template_key     text,
              ADD COLUMN IF NOT EXISTS prompt_template_version integer
            """
        )

        # The view itself — CREATE OR REPLACE so a replay is idempotent
        # even after a column rename / add in a later migration. (DROP
        # + recreate would be required if we changed the column ORDER
        # of the SELECT list; we use OR REPLACE here because the
        # initial creation has a stable shape.)
        await conn.execute(_VIEW_SQL)
        # COMMENT ON is DDL — Postgres rejects parameterized DDL, so
        # we inline the constant. The comment body is fixed text (no
        # operator data, no escaping needed).
        await conn.execute(
            "COMMENT ON VIEW lab_outcomes_v1 IS "
            "'Unified read surface for the content R&D lab — joins "
            "capability_outcomes + routing_outcomes + "
            "published_post_edit_metrics + page_views per task. "
            "Phase 0 (2026-05-28). Bandit/dashboards/learnings digest "
            "read from this view.'"
        )

    logger.info(
        "[migration] lab_observability_columns_and_view: applied — "
        "lab_outcomes_v1 + 9 columns across 3 tables",
    )


async def down(pool) -> None:
    """Revert the migration. Drops the view first (depends on the
    columns), then the columns. Tables themselves are untouched.

    Per ``feedback_finish_migrations``: both directions actually do
    the work. The view is computed (no data loss); the columns are
    additive and nullable so dropping them only loses Phase 0
    observation data that hasn't been useful long enough to be load-
    bearing yet.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP VIEW IF EXISTS lab_outcomes_v1")
        await conn.execute(
            """
            ALTER TABLE published_post_edit_metrics
              DROP COLUMN IF EXISTS prompt_template_version,
              DROP COLUMN IF EXISTS prompt_template_key,
              DROP COLUMN IF EXISTS model_used
            """
        )
        await conn.execute(
            """
            ALTER TABLE routing_outcomes
              DROP COLUMN IF EXISTS prompt_template_version,
              DROP COLUMN IF EXISTS prompt_template_key,
              DROP COLUMN IF EXISTS niche_slug
            """
        )
        await conn.execute(
            "DROP INDEX IF EXISTS idx_capability_outcomes_niche_template"
        )
        await conn.execute(
            """
            ALTER TABLE capability_outcomes
              DROP COLUMN IF EXISTS prompt_template_version,
              DROP COLUMN IF EXISTS prompt_template_key,
              DROP COLUMN IF EXISTS niche_slug
            """
        )
    logger.info(
        "[migration] lab_observability_columns_and_view down: "
        "view + 9 columns removed",
    )
