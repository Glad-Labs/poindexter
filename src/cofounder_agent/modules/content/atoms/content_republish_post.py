"""content.republish_post — terminal atom of the seo_refresh graph.

Applies the optimized meta to the live post (meta_only: seo_title/
seo_description/seo_keywords — NEVER content), re-exports the static JSON to R2,
fires ISR revalidation (a DB update alone does NOT reach the live site), and
stamps the seo_opportunities row with the pre-refresh baseline + status='refreshed'
for later outcome measurement.

Only reached after atoms.approval_gate passes, so execution implies sign-off.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.revalidation_service import trigger_isr_revalidate
from services.static_export_service import export_post

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.republish_post",
    type="atom",
    version="1.0.0",
    description=(
        "Apply optimized meta to a live post (meta_only), re-export to R2, "
        "revalidate ISR, and stamp the seo_opportunities baseline. Gated — runs "
        "only after approval_gate."
    ),
    inputs=(
        FieldSpec(name="post_id", type="str", description="posts.id (uuid)"),
        FieldSpec(name="post_slug", type="str", description="post slug for export/revalidate"),
        FieldSpec(name="seo_title", type="str", description="optimized title"),
        FieldSpec(name="seo_description", type="str", description="optimized meta"),
        FieldSpec(
            name="seo_keywords",
            type="str",
            description="comma-separated keywords",
            required=False,
        ),
        FieldSpec(
            name="seo_opportunity_id",
            type="str",
            description="seo_opportunities.id — stamped with baseline metrics",
            required=False,
        ),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="site_config", type="object", description="SiteConfig"),
    ),
    outputs=(
        FieldSpec(name="status", type="str", description="'refreshed' on success"),
    ),
    requires=("post_id", "post_slug"),
    produces=("status",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("db_write", "r2_export", "isr_revalidate"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_UPDATE_POST_SQL = """
UPDATE posts
   SET seo_title       = $2,
       seo_description = $3,
       seo_keywords    = $4,
       updated_at      = NOW()
 WHERE id = $1::uuid
"""

_STAMP_OPP_SQL = """
UPDATE seo_opportunities
   SET status            = 'refreshed',
       baseline_position = current_position,
       baseline_ctr      = ctr
 WHERE id = $1::uuid
"""
# Baseline is stamped self-referentially from the opportunity's own current
# metrics — no need to thread current_position/ctr through PipelineState.


async def run(state: dict[str, Any]) -> dict[str, Any]:
    post_id = state.get("post_id")
    slug = state.get("post_slug") or ""
    db = state.get("database_service")
    pool = getattr(db, "pool", None)
    site_config = state.get("site_config")

    if not post_id or not slug or pool is None:
        raise RuntimeError(
            f"content.republish_post: post_id+post_slug+pool required "
            f"(post_id={post_id!r}, slug={slug!r})"
        )

    seo_title = state.get("seo_title") or ""
    seo_description = state.get("seo_description") or ""
    seo_keywords = state.get("seo_keywords") or ""

    async with pool.acquire() as conn:
        await conn.execute(_UPDATE_POST_SQL, str(post_id), seo_title, seo_description, seo_keywords)
        opp_id = state.get("seo_opportunity_id")
        if opp_id:
            await conn.execute(_STAMP_OPP_SQL, str(opp_id))

    # Propagation — a DB update alone does NOT reach the live site (R2 JSON +
    # ISR). Await inline (export) per the cancelled-bg-task lesson in publish_service.
    exported = await export_post(pool, slug, site_config=site_config)
    revalidated = await trigger_isr_revalidate(slug, site_config=site_config)
    logger.info(
        "[content.republish_post] post=%s slug=%s exported=%s revalidated=%s",
        post_id,
        slug,
        exported,
        revalidated,
    )
    return {"status": "refreshed"}


__all__ = ["ATOM_META", "run"]
