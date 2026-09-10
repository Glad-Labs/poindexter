"""Scheduled job: auto-enqueue seo_refresh tasks from open SEO opportunities.

SEO Harvest Loop Phase 2b (#763). Picks the top-N open opportunities by
gap_score and creates a gated seo_refresh pipeline_task for each, skipping any
post that already has an ACTIVE refresh task, then parks the opportunity at
status='queued'. Content-MUTATING (creates a gated task) — gates on
seo.refresh.enabled (default off). The approval gate (seo_refresh_gate, ships
enabled) still requires operator sign-off before any republish, so the worst
case of a bug here is an extra task that pauses for review.

Dedup: the task<->post link is pipeline_versions.stage_data->'task_metadata'->>
'post_id' (the seam tasks_db.add_task writes and content_router._load_task_metadata
reads). "Active" is an explicit whitelist of in-flight statuses so a future
status can't slip through as active.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.tasks_db import TasksDatabase
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SELECT_CANDIDATES_SQL = """
SELECT o.id AS opportunity_id, o.post_id, o.slug, o.target_query, o.gap_score, o.tier
FROM seo_opportunities o
WHERE o.status = 'open'
  AND o.post_id IS NOT NULL
  AND o.impressions >= $2
  AND NOT EXISTS (
      SELECT 1
      FROM pipeline_tasks t
      JOIN pipeline_versions v ON v.task_id = t.task_id
      WHERE t.template_slug = 'seo_refresh'
        AND t.status IN ('pending','in_progress','awaiting_gate','awaiting_approval')
        AND v.stage_data->'task_metadata'->>'post_id' = o.post_id::text
  )
-- A meta_only refresh moves CTR, not ranking, so target the tiers where the
-- meta IS the lever (page1_push / low_ctr) before striking_distance, whose
-- page-2 posts are ranking-limited — meta can't lift a metric that stays ~0 at
-- position 15. gap_score orders within each bucket. (SEO targeting audit, 2026-07.)
ORDER BY (CASE WHEN o.tier = 'striking_distance' THEN 1 ELSE 0 END),
         o.gap_score DESC
LIMIT $1
"""

_MARK_QUEUED_SQL = "UPDATE seo_opportunities SET status='queued' WHERE id=$1::uuid"


class EnqueueSeoRefreshesJob:
    name = "enqueue_seo_refreshes"
    description = (
        "Auto-enqueue seo_refresh tasks from the top open SEO opportunities "
        "(gated on seo.refresh.enabled; capped by seo.refresh.max_per_run)"
    )
    schedule = "every 6 hours"
    idempotent = False  # creates tasks

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None or not sc.get_bool("seo.refresh.enabled", False):
            return JobResult(ok=True, detail="seo.refresh.enabled is off; skipped")

        max_per_run = int(sc.get_float("seo.refresh.max_per_run", 3))
        # Spend floor — a meta refresh needs real search demand to convert, and
        # every refresh costs an operator a gate review. Distinct from the
        # classifier's per-tier floor (which governs what becomes an
        # opportunity at all): this governs what is worth refreshing NOW, and
        # also screens stale 'open' rows classified before a floor existed.
        min_impressions = int(sc.get_float("seo.refresh.min_impressions", 100))
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    _SELECT_CANDIDATES_SQL, max_per_run, min_impressions
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[enqueue_seo_refreshes] candidate query failed: %s", describe_exception(e), exc_info=True
            )
            return JobResult(
                ok=False, detail=f"candidate query failed: {describe_exception(e)}"
            )

        tasks_db = TasksDatabase(pool)
        queued: list[dict[str, Any]] = []
        for r in rows:
            try:
                task_id = await tasks_db.add_task(
                    {
                        "task_type": "seo_refresh",
                        "template_slug": "seo_refresh",
                        "topic": r["slug"],
                        "status": "pending",
                        "task_metadata": {
                            "post_id": str(r["post_id"]),
                            "seo_opportunity_id": str(r["opportunity_id"]),
                            "target_query": r["target_query"] or "",
                        },
                    }
                )
                async with pool.acquire() as conn:
                    await conn.execute(_MARK_QUEUED_SQL, str(r["opportunity_id"]))
                queued.append(
                    {
                        "slug": r["slug"],
                        "gap_score": float(r["gap_score"] or 0),
                        "task_id": task_id,
                    }
                )
            except Exception as e:  # noqa: BLE001 — one bad candidate never aborts the run
                logger.warning(
                    "[enqueue_seo_refreshes] enqueue failed for %s: %s", r["slug"], describe_exception(e)
                )

        if queued:
            body = "## SEO refresh — queued for review\n\n" + "\n".join(
                f"- **{q['slug']}** — gap≈{q['gap_score']:.0f} clicks/mo (task {q['task_id']})"
                for q in queued
            )
            emit_finding(
                source="enqueue_seo_refreshes",
                kind="seo_refresh_queued",
                title=f"SEO: {len(queued)} refresh task(s) queued for sign-off",
                body=body,
                # 'warn' so findings_alert_router fetches it (it filters out
                # 'info'); findings.seo_refresh_queued.delivery='discord' then
                # pins the ops channel. Routine notification, not a page.
                severity="warn",
                extra={"count": len(queued)},
            )

        logger.info("[enqueue_seo_refreshes] queued %d refresh task(s)", len(queued))
        return JobResult(
            ok=True,
            detail=f"queued {len(queued)} refresh task(s)",
            changes_made=len(queued),
            metrics={"queued": len(queued)},
        )
