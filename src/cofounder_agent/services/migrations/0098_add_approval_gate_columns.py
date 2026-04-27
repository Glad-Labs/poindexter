"""Migration 0098: HITL approval-gate columns on ``pipeline_tasks`` (#145).

The blog pipeline already had ONE final-approval checkpoint at
``finalize_task`` — auto-publish if score >= 80, else status flips to
``awaiting_approval`` + a Telegram/Discord notify, then the operator
runs ``/approve_post`` or ``/reject_post``.

#145 generalizes that: a Stage-author should be able to drop a
mid-pipeline approval gate at any boundary (topic decision, preview
image, draft, final media) with config — no schema changes per gate.

Schema location
---------------

Issue #145 phrased the schema sketch in terms of ``content_tasks``.
On this stack ``content_tasks`` is a VIEW over the underlying
``pipeline_tasks`` BASE TABLE (see migration 0007 + the post-Phase-D
view consolidation). ALTER TABLE refuses to add columns to a view,
so the new HITL columns have to live on ``pipeline_tasks`` directly.
The approval-service helpers and the ``ApprovalGateStage`` follow
that — they read/write ``pipeline_tasks``. The ``content_tasks`` view
is then re-created so the new columns surface uniformly to the
read-side dashboards (Grafana, ``poindexter list-pending``, etc.).

Three new columns capture the gate state. They are nullable /
default-empty so existing rows are unaffected:

- ``awaiting_gate``  VARCHAR(64) — name of the gate the task is paused
  at, or NULL when the task isn't paused. Examples:
  ``"topic_decision"``, ``"preview_approval"``, ``"final_media"``.
  Independent of ``status`` so a paused task can stay
  ``status='in_progress'``; the dashboard query for "anything waiting
  on a human" is just ``WHERE awaiting_gate IS NOT NULL``.
- ``gate_artifact``  JSONB        — the thing under review. Shape varies
  per gate; for ``topic_decision`` it's
  ``{"topic": "...", "rationale": "..."}``; for ``preview_approval``
  it's ``{"image_url": "...", "alt_text": "..."}``. The CLI / MCP
  read this when rendering ``poindexter show-pending``.
- ``gate_paused_at`` TIMESTAMPTZ  — when the pipeline halted. Lets the
  staleness sweeper auto-reject gates that have been pending longer
  than ``approval_gate_max_age_hours`` (a follow-up).

Migration is idempotent: ``ADD COLUMN IF NOT EXISTS`` on every column
+ ``CREATE INDEX IF NOT EXISTS`` on the partial index. The
``content_tasks`` view re-creation drops + creates so both first run
and any retry land on a known-good shape. The down path rolls back
the column adds and drops the view (operators rolling back should
re-apply whichever earlier migration originally defined the view).

The existing ``status='awaiting_approval'`` flow at ``finalize_task``
keeps working unchanged — it just ignores the new columns. A
follow-up will migrate that final-media gate to use
``awaiting_gate='final_media'`` so the operator interface is uniform.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_NEW_COLUMNS = [
    ("awaiting_gate", "VARCHAR(64) NULL"),
    ("gate_artifact", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("gate_paused_at", "TIMESTAMPTZ NULL"),
]


async def _recreate_content_tasks_view(conn) -> None:
    """Drop + re-create the ``content_tasks`` view to surface the new
    HITL columns. Definition mirrors the post-Phase-D consolidation
    output of ``pg_get_viewdef('content_tasks')`` with ``awaiting_gate``,
    ``gate_artifact``, and ``gate_paused_at`` appended at the end of
    the column list."""
    await conn.execute("DROP VIEW IF EXISTS content_tasks")
    await conn.execute(
        """
        CREATE VIEW content_tasks AS
        SELECT
            pt.id,
            pt.task_id,
            pt.task_type,
            pt.task_type AS content_type,
            pv.title,
            pt.topic,
            pt.status,
            pt.stage,
            pt.style,
            pt.tone,
            pt.target_length,
            pt.category,
            pt.primary_keyword,
            pt.target_audience,
            pv.content,
            pv.excerpt,
            pv.featured_image_url,
            pv.quality_score,
            pv.qa_feedback,
            pv.seo_title,
            pv.seo_description,
            pv.seo_keywords,
            pt.percentage,
            pt.message,
            pt.model_used,
            pt.error_message,
            pv.models_used_by_phase,
            COALESCE(pv.stage_data -> 'metadata'::text, pv.stage_data) AS metadata,
            COALESCE(pv.stage_data -> 'result'::text, pv.stage_data) AS result,
            COALESCE(pv.stage_data -> 'task_metadata'::text, pv.stage_data) AS task_metadata,
            pt.site_id,
            pt.created_at,
            pt.updated_at,
            pt.started_at,
            pt.completed_at,
            (SELECT pr.decision
               FROM pipeline_reviews pr
              WHERE pr.task_id::text = pt.task_id::text
              ORDER BY pr.created_at DESC
              LIMIT 1) AS approval_status,
            (SELECT pr.reviewer
               FROM pipeline_reviews pr
              WHERE pr.task_id::text = pt.task_id::text
              ORDER BY pr.created_at DESC
              LIMIT 1) AS approved_by,
            (SELECT pr.feedback
               FROM pipeline_reviews pr
              WHERE pr.task_id::text = pt.task_id::text
              ORDER BY pr.created_at DESC
              LIMIT 1) AS human_feedback,
            (SELECT pd.post_id
               FROM pipeline_distributions pd
              WHERE pd.task_id::text = pt.task_id::text
                AND pd.target::text = 'gladlabs.io'::text
              LIMIT 1) AS post_id,
            (SELECT pd.post_slug
               FROM pipeline_distributions pd
              WHERE pd.task_id::text = pt.task_id::text
                AND pd.target::text = 'gladlabs.io'::text
              LIMIT 1) AS post_slug,
            (SELECT pd.published_at
               FROM pipeline_distributions pd
              WHERE pd.task_id::text = pt.task_id::text
                AND pd.target::text = 'gladlabs.io'::text
              LIMIT 1) AS published_at,
            -- HITL gate state (#145). Pulls straight from pipeline_tasks
            -- so dashboards / CLI list-pending see them with no extra
            -- join.
            pt.awaiting_gate,
            pt.gate_artifact,
            pt.gate_paused_at
        FROM pipeline_tasks pt
        LEFT JOIN pipeline_versions pv
               ON pv.task_id::text = pt.task_id::text
              AND pv.version = (
                  SELECT MAX(pipeline_versions.version)
                    FROM pipeline_versions
                   WHERE pipeline_versions.task_id::text = pt.task_id::text
              )
        """
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for column, ddl in _NEW_COLUMNS:
            await conn.execute(
                f"ALTER TABLE pipeline_tasks "
                f"ADD COLUMN IF NOT EXISTS {column} {ddl}"
            )

        # Partial index — the dashboard query is "anything waiting on a
        # human." A full BTREE on awaiting_gate would index every NULL
        # row; a WHERE-clause partial index keeps the index footprint
        # tiny (one row per paused task).
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_awaiting_gate "
            "ON pipeline_tasks (awaiting_gate, gate_paused_at) "
            "WHERE awaiting_gate IS NOT NULL"
        )

        await _recreate_content_tasks_view(conn)

        logger.info(
            "0098: extended pipeline_tasks with %d HITL approval-gate "
            "columns + re-created content_tasks view",
            len(_NEW_COLUMNS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Drop the view first so the table-level ALTER won't fail on
        # column dependencies.
        await conn.execute("DROP VIEW IF EXISTS content_tasks")
        await conn.execute(
            "DROP INDEX IF EXISTS idx_pipeline_tasks_awaiting_gate"
        )
        for column, _ddl in reversed(_NEW_COLUMNS):
            await conn.execute(
                f"ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS {column}"
            )
        # Note: we don't auto-restore the view here — the migration
        # runner doesn't track view definitions. Operators rolling back
        # 0098 should re-apply whichever earlier migration originally
        # defined ``content_tasks`` (post-Phase-D consolidation).
        logger.info("0098: rolled back HITL approval-gate columns")
