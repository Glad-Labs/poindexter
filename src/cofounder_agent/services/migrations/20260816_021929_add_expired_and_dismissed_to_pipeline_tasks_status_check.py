"""Migration 20260816_021929: add 'expired' + 'dismissed' to pipeline_tasks_status_check.

ISSUE: Glad-Labs/poindexter#981

Two writers target statuses the check constraint never learned, so both
crash with a constraint violation exactly when they have real work to do:

- ``ExpireStaleApprovalsJob`` does ``SET status = 'expired'`` on tasks
  stuck in ``awaiting_approval`` past TTL. Crashed in prod 2026-07-23 and
  2026-08-06 (``job-fail:expire_stale_approvals`` findings) — it has never
  successfully expired anything, so it never relieved a full approval
  queue.
- ``approval_service.reject_gate`` writes ``'dismissed'`` when a gate
  rejection resolves to the dismiss status (``status_override='dismissed'``
  from ``ExpireStaleSeoRefreshGatesJob``, or a per-gate
  ``approval_gate_<gate>_reject_status`` setting). Zero ``dismissed`` rows
  exist in prod — the path is a latent instance of the same crash.

Three DB-side artifacts change, not just the constraint: the
``content_tasks_update_redirect`` trigger function carries its own
``completed_at`` status CASE (writes through the view get completed_at
from THAT list — ``NEW.completed_at`` is ignored), and the
``checkpoint_prune`` retention policy row enumerates terminal statuses in
its config — a status missing there is never pruned at all.

Python-side consumers (social-draft orphan reaper, chat watch/plans
terminal sets, CLI status styling/filters, ``pipeline_db`` completed_at
stamping, ``set_task_status`` validation) were swept in the same change.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The full allowed set = the 15 baseline statuses + the two this migration
# adds. Kept as a module constant so tests can import it instead of
# re-parsing SQL.
PIPELINE_TASK_STATUSES: tuple[str, ...] = (
    "pending",
    "in_progress",
    "approved",
    "awaiting_approval",
    "awaiting_gate",
    "rejected",
    "rejected_retry",
    "rejected_final",
    "failed",
    "completed",
    "published",
    "cancelled",
    "dry_run",
    "superseded",
    "archived",
    # Added by this migration:
    "expired",
    "dismissed",
)

# Statuses that stamp ``completed_at`` — must stay in lockstep with the
# CASE in services/pipeline_db.py (the Python-side twin of the view
# trigger this migration replaces). The last two are this migration's
# additions; [:-2] is the pre-migration list ``down()`` restores.
COMPLETED_AT_STATUSES: tuple[str, ...] = (
    "published",
    "failed",
    "cancelled",
    "rejected",
    "rejected_final",
    "expired",
    "dismissed",
)


def _constraint_sql(statuses: tuple[str, ...]) -> tuple[str, str]:
    quoted = ", ".join(f"'{s}'" for s in statuses)
    return (
        "ALTER TABLE pipeline_tasks "
        "DROP CONSTRAINT IF EXISTS pipeline_tasks_status_check",
        "ALTER TABLE pipeline_tasks "
        "ADD CONSTRAINT pipeline_tasks_status_check "
        f"CHECK (status IN ({quoted}))",
    )


def _redirect_trigger_sql(completed_at_statuses: tuple[str, ...]) -> str:
    """The content_tasks_update_redirect body, replicated from
    0000_baseline.schema.sql with only the completed_at CASE list
    parameterised."""
    completed_quoted = ", ".join(f"'{s}'" for s in completed_at_statuses)
    return f"""
        CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
        BEGIN
            UPDATE pipeline_tasks SET
                status = NEW.status,
                stage = COALESCE(NEW.stage, pipeline_tasks.stage),
                percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
                message = COALESCE(NEW.message, pipeline_tasks.message),
                model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
                error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
                style = COALESCE(NEW.style, pipeline_tasks.style),
                tone = COALESCE(NEW.tone, pipeline_tasks.tone),
                target_audience = COALESCE(NEW.target_audience, pipeline_tasks.target_audience),
                primary_keyword = COALESCE(NEW.primary_keyword, pipeline_tasks.primary_keyword),
                target_length = COALESCE(NEW.target_length, pipeline_tasks.target_length),
                updated_at = COALESCE(NEW.updated_at, NOW()),
                started_at = COALESCE(NEW.started_at, pipeline_tasks.started_at),
                completed_at = CASE
                    WHEN NEW.status IN ({completed_quoted})
                    THEN NOW()
                    ELSE pipeline_tasks.completed_at
                END
            WHERE task_id = NEW.task_id;

            UPDATE pipeline_versions SET
                title = COALESCE(NEW.title, pipeline_versions.title),
                content = COALESCE(NEW.content, pipeline_versions.content),
                excerpt = COALESCE(NEW.excerpt, pipeline_versions.excerpt),
                featured_image_url = COALESCE(NEW.featured_image_url, pipeline_versions.featured_image_url),
                seo_title = COALESCE(NEW.seo_title, pipeline_versions.seo_title),
                seo_description = COALESCE(NEW.seo_description, pipeline_versions.seo_description),
                seo_keywords = COALESCE(NEW.seo_keywords, pipeline_versions.seo_keywords),
                quality_score = COALESCE(NEW.quality_score, pipeline_versions.quality_score),
                qa_feedback = COALESCE(NEW.qa_feedback, pipeline_versions.qa_feedback),
                models_used_by_phase = COALESCE(NEW.models_used_by_phase, pipeline_versions.models_used_by_phase),
                stage_data = pipeline_versions.stage_data || jsonb_strip_nulls(
                    jsonb_build_object(
                        'metadata', NEW.metadata,
                        'result', NEW.result,
                        'task_metadata', NEW.task_metadata
                    )
                )
            WHERE task_id = NEW.task_id AND version = 1;

            RETURN NEW;
        END;
        $$
    """  # nosec B608 - DDL from module-constant status tuples; no external input reaches the SQL


async def up(pool) -> None:
    """Swap the constraint, extend the view trigger's completed_at CASE,
    and teach the checkpoint_prune policy row the two new terminal
    statuses. DROP IF EXISTS + ADD is idempotent as a pair; the policy
    append is containment-guarded."""
    drop_sql, add_sql = _constraint_sql(PIPELINE_TASK_STATUSES)
    async with pool.acquire() as conn:
        await conn.execute(drop_sql)
        await conn.execute(add_sql)
        await conn.execute(_redirect_trigger_sql(COMPLETED_AT_STATUSES))
        # The checkpoint_prune retention policy enumerates terminal statuses
        # in its DB config row (seeded by 0000_baseline.seeds.sql, which
        # existing installs never re-run) — a status missing from it is
        # never pruned at all. Append the two new terminal statuses,
        # idempotently and without clobbering operator customization.
        for status in ("expired", "dismissed"):
            await conn.execute(
                """
                UPDATE retention_policies
                   SET config = jsonb_set(
                         config, '{terminal_statuses}',
                         (config->'terminal_statuses') || to_jsonb($1::text)
                       )
                 WHERE handler_name = 'checkpoint_prune'
                   AND config ? 'terminal_statuses'
                   AND NOT config->'terminal_statuses' @> to_jsonb($1::text)
                """,
                status,
            )
    logger.info(
        "Migration add_expired_and_dismissed_to_pipeline_tasks_status_check: applied"
    )


async def down(pool) -> None:
    """Restore the 15-value constraint, the original trigger CASE, and the
    seeded policy list.

    The constraint re-add fails loudly (validation) if any rows already
    hold 'expired' or 'dismissed' — resolve those rows explicitly before
    reverting; silently rewriting statuses is not this migration's call.
    """
    drop_sql, add_sql = _constraint_sql(PIPELINE_TASK_STATUSES[:-2])
    async with pool.acquire() as conn:
        await conn.execute(drop_sql)
        await conn.execute(add_sql)
        await conn.execute(_redirect_trigger_sql(COMPLETED_AT_STATUSES[:-2]))
        await conn.execute(
            """
            UPDATE retention_policies
               SET config = jsonb_set(
                     config, '{terminal_statuses}',
                     (config->'terminal_statuses') - 'expired' - 'dismissed'
                   )
             WHERE handler_name = 'checkpoint_prune'
               AND config ? 'terminal_statuses'
            """
        )
