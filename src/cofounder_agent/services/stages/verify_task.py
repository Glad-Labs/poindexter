"""VerifyTaskStage — confirm the task row exists before the pipeline works on it.

First stage in the content-generation pipeline. Cheap sanity check: the
legacy code hit a bug once where a task_id was fabricated upstream and
the entire pipeline ran before anyone noticed no DB row existed. This
stage catches that failure at the top of the pipeline with zero
speculative work done.

Context reads:
- ``task_id`` (str, required)
- ``database_service`` (required — used to look up the task)

Context writes:
- ``stages["1_content_task_created"]`` (bool)
- ``content_task_id`` (str, echoed)

Phase E migration notes:
- Replaces ``_stage_verify_task`` in services/content_router_service.py
- Preserves exact observable behavior (log messages, result-dict keys)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class VerifyTaskStage:
    name = "verify_task"
    description = "Confirm the content_tasks row exists before running the pipeline"
    # Cheap lookup — 2s timeout is generous.
    timeout_seconds = 10
    halts_on_failure = False  # The legacy stage never raised; it just warned.

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        task_id = context.get("task_id")
        database_service = context.get("database_service")

        if not task_id:
            return StageResult(
                ok=False,
                detail="context missing task_id",
                continue_workflow=True,  # Legacy behavior: log, continue.
            )
        if database_service is None:
            return StageResult(
                ok=False,
                detail="context missing database_service",
                continue_workflow=True,
            )

        stages: dict[str, Any] = context.setdefault("stages", {})

        logger.info("STAGE 1: Verifying task record exists...")
        try:
            existing = await database_service.get_task(task_id)
        except Exception as e:
            logger.error("Failed to verify task: %s", e, exc_info=True)
            stages["1_content_task_created"] = False
            return StageResult(
                ok=False,
                detail=f"DB lookup raised: {e}",
                context_updates={"stages": stages},
                continue_workflow=True,
            )

        if existing:
            logger.info("Task verified in database: %s", task_id)
            stages["1_content_task_created"] = True
            return StageResult(
                ok=True,
                detail="task verified",
                context_updates={
                    "content_task_id": task_id,
                    "stages": stages,
                },
            )

        logger.warning("Task %s not found - this should not happen", task_id)
        stages["1_content_task_created"] = False
        return StageResult(
            ok=False,
            detail="task_id not found in DB",
            context_updates={"stages": stages},
            continue_workflow=True,  # Legacy: log and continue.
        )
