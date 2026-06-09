"""Re-evaluate pending Gate-2 rows for earned-autonomy eligibility (#531).

``record_pending`` evaluates the three approval tiers at insert time, so a
niche's pending rows only benefit from Tier-2 (earned autonomy) if the
dispatch track record already meets the threshold *when the asset is seeded*.
This job fixes the retrospective gap: it runs periodically, re-checks all
``status='pending'`` Gate-2 rows, and bulk-promotes any where the niche has
since accumulated enough consecutive dispatch successes.

Flow
----
1. Query distinct ``(niche_slug, medium)`` combos with at least one
   ``status='pending'`` row in ``media_approvals``.
2. For each combo, call ``_earned_autonomy_check`` (the same check used at
   seeding).
3. If the check passes, UPDATE all pending rows for that combo to
   ``status='approved'`` / ``decided_by='auto:earned_autonomy:<slug>'``.
4. Emit an audit finding per promoted row so the operator sees the upgrade in
   the Findings dashboard.

The master switch ``media.gate2.earned_autonomy_enabled`` is read inside
``_earned_autonomy_check`` itself; if it's ``false`` the check returns
``False`` for every combo and the job is effectively a no-op.

Master switch for the job itself is the same
``media_pipeline_trigger_enabled`` gate used by all Stage-2 jobs — if
Stage-2 hasn't been turned on there's no Gate-2 queue to re-evaluate.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.media_approval_service import _earned_autonomy_check
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_PENDING_COMBOS_SQL = """
SELECT DISTINCT
    pt.niche_slug,
    ma.medium
FROM media_approvals ma
JOIN posts p ON p.id = ma.post_id
JOIN pipeline_tasks pt
    ON pt.task_id = (p.metadata ->> 'pipeline_task_id')
WHERE ma.status = 'pending'
  AND pt.niche_slug IS NOT NULL
"""

_PROMOTE_SQL = """
UPDATE media_approvals ma
SET
    status      = 'approved',
    decided_by  = $3,
    decided_at  = NOW(),
    updated_at  = NOW()
FROM posts p
JOIN pipeline_tasks pt
    ON pt.task_id = (p.metadata ->> 'pipeline_task_id')
WHERE ma.post_id = p.id
  AND ma.status  = 'pending'
  AND ma.medium  = $2
  AND pt.niche_slug = $1
RETURNING ma.id, ma.post_id
"""


class EvaluateEarnedAutonomyGate2Job:
    name = "evaluate_earned_autonomy_gate2"
    description = (
        "Re-evaluate pending Gate-2 approval rows against the current dispatch "
        "track record and bulk-promote any that now meet the earned-autonomy "
        "threshold (#531). Gated by media_pipeline_trigger_enabled."
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)
        if pool is None:
            return JobResult(ok=True, detail="no pool — skipping", changes_made=0)

        if not sc.get_bool("media_pipeline_trigger_enabled", False):
            return JobResult(
                ok=True,
                detail="media_pipeline_trigger_enabled=false — dormant",
                changes_made=0,
            )

        try:
            combos = await pool.fetch(_PENDING_COMBOS_SQL)
        except Exception as exc:
            logger.warning("[GATE2_EVAL] pending-combos query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {exc}", changes_made=0)

        if not combos:
            return JobResult(ok=True, detail="no pending Gate-2 rows", changes_made=0)

        promoted = 0
        async with pool.acquire() as conn:
            for row in combos:
                niche_slug = row["niche_slug"]
                medium = row["medium"]
                try:
                    eligible = await _earned_autonomy_check(conn, niche_slug, medium)
                except Exception as exc:
                    logger.warning(
                        "[GATE2_EVAL] eligibility check failed niche=%s medium=%s: %s",
                        niche_slug, medium, exc,
                    )
                    continue

                if not eligible:
                    continue

                decided_by = f"auto:earned_autonomy:{niche_slug}"
                try:
                    promoted_rows = await conn.fetch(
                        _PROMOTE_SQL, niche_slug, medium, decided_by,
                    )
                except Exception as exc:
                    logger.warning(
                        "[GATE2_EVAL] promote failed niche=%s medium=%s: %s",
                        niche_slug, medium, exc,
                    )
                    continue

                for pr in promoted_rows:
                    promoted += 1
                    emit_finding(
                        source="evaluate_earned_autonomy_gate2",
                        kind="media_earned_autonomy_granted",
                        title=(
                            f"Gate-2 auto-approved: {medium} for {niche_slug} "
                            "(earned-autonomy threshold met)"
                        ),
                        body=(
                            f"post {pr['post_id']}: pending Gate-2 row promoted via "
                            f"earned-autonomy re-evaluation. "
                            f"decided_by={decided_by}."
                        ),
                        severity="info",
                        dedup_key=f"media_earned_autonomy:{pr['post_id']}:{medium}",
                        extra={
                            "approval_id": str(pr["id"]),
                            "niche_slug": niche_slug,
                            "medium": medium,
                            "decided_by": decided_by,
                        },
                    )

                logger.info(
                    "[GATE2_EVAL] promoted %d pending rows — niche=%s medium=%s",
                    len(promoted_rows), niche_slug, medium,
                )

        detail = f"promoted {promoted} pending Gate-2 row(s) to approved"
        logger.info("[GATE2_EVAL] complete — %s", detail)
        return JobResult(ok=True, detail=detail, changes_made=promoted)


__all__ = ["EvaluateEarnedAutonomyGate2Job"]
