"""Migration: page_views.is_bot flag + page_views_human view + lab_outcomes_v1 repoint.

De-bots the first-party beacon KPI. Stealth scrapers present a browser UA and
slip the sync job's narrow UA drop-filter, inflating page_views ~10x (one Linux
Chrome/149 UA = 90% of a 28-day window). This migration adds the materialized
classification surface; FlagBotPageViewsJob populates it. The one-time backfill
of existing rows lives in that job (a migration runs once; the job's
sentinel-guarded backfill is re-evaluated every boot and is correct on fresh
installs).

Idempotent DDL — ADD COLUMN / CREATE INDEX IF NOT EXISTS no-op on fresh installs
(where nothing yet has the column) and do the real add on prod (baseline
predates is_bot). lab_outcomes_v1 is reproduced verbatim from the baseline with
the single change page_views -> page_views_human in its LATERAL subquery; the
column list is unchanged, so the dependent view experiment_variant_scorecard_v1
is unaffected.

stdlib-only so migrations-smoke applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# lab_outcomes_v1 reproduced verbatim from 0000_baseline.schema.sql with the
# page_views reference parameterised so up()/down() can point it at the human
# view or back at the raw table. Column list/order is unchanged.
_LAB_OUTCOMES_BODY = """
CREATE OR REPLACE VIEW public.lab_outcomes_v1 AS
 SELECT co.task_id,
    co.niche_slug,
    co.template_slug,
    co.atom_name,
    co.model_used,
    co.prompt_template_key,
    co.prompt_template_version,
    co.ok AS atom_ok,
    co.halted AS atom_halted,
    co.quality_score AS atom_quality_score,
    co.elapsed_ms,
    co.created_at AS run_at,
    ro.actual_cost,
    ro.estimated_cost,
    ro.compute_tier,
    ro.success AS routing_success,
    pem.approver,
    pem.char_diff_count,
    pem.line_diff_count,
    pem.pre_approve_len,
    pem.post_approve_len,
    pem.approve_method,
    pem.approved_at,
    pv_count.views_24h AS views_24h_post_publish,
    pv_count.views_7d AS views_7d_post_publish,
    ev.label AS variant_label,
    ev.id AS variant_id,
    e.key AS experiment_key,
    e.status AS experiment_status,
    e.objective_function AS experiment_objective_function
   FROM (((((public.capability_outcomes co
     LEFT JOIN public.routing_outcomes ro ON (((ro.task_id)::text = co.task_id)))
     LEFT JOIN public.published_post_edit_metrics pem ON ((pem.task_id = co.task_id)))
     LEFT JOIN LATERAL ( SELECT count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '24:00:00'::interval)))) AS views_24h,
            count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '7 days'::interval)))) AS views_7d
           FROM ({page_views_ref} pv
             JOIN public.posts p ON (((p.slug)::text = (pv.slug)::text)))
          WHERE ((pem.approved_at IS NOT NULL) AND ((p.metadata ->> 'pipeline_task_id'::text) = co.task_id))) pv_count ON (true))
     LEFT JOIN public.experiment_variants ev ON ((ev.id = co.variant_id)))
     LEFT JOIN public.experiments e ON ((e.id = ev.experiment_id)))
  WHERE (co.created_at > (now() - '90 days'::interval));
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                ALTER TABLE page_views
                    ADD COLUMN IF NOT EXISTS is_bot boolean NOT NULL DEFAULT false,
                    ADD COLUMN IF NOT EXISTS bot_reason text,
                    ADD COLUMN IF NOT EXISTS flagged_at timestamp with time zone
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_page_views_human_created "
                "ON page_views (created_at) WHERE is_bot = false"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_page_views_human_slug "
                "ON page_views (slug) WHERE is_bot = false"
            )
            await conn.execute(
                "CREATE OR REPLACE VIEW public.page_views_human AS "
                "SELECT id, path, slug, referrer, user_agent, created_at "
                "FROM public.page_views WHERE is_bot = false"
            )
            await conn.execute(
                _LAB_OUTCOMES_BODY.format(page_views_ref="public.page_views_human")
            )
    logger.info(
        "page_views_is_bot_flag up: is_bot column + page_views_human view + "
        "lab_outcomes_v1 repointed to human view"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Repoint lab_outcomes_v1 back to raw page_views BEFORE dropping the
            # human view it now depends on.
            await conn.execute(
                _LAB_OUTCOMES_BODY.format(page_views_ref="public.page_views")
            )
            await conn.execute("DROP VIEW IF EXISTS public.page_views_human")
            await conn.execute("DROP INDEX IF EXISTS idx_page_views_human_created")
            await conn.execute("DROP INDEX IF EXISTS idx_page_views_human_slug")
            await conn.execute(
                "ALTER TABLE page_views "
                "DROP COLUMN IF EXISTS is_bot, "
                "DROP COLUMN IF EXISTS bot_reason, "
                "DROP COLUMN IF EXISTS flagged_at"
            )
    logger.info("page_views_is_bot_flag down: reverted to raw page_views")
