"""WriterSelfReviewStage — stage 2A.5 of the content pipeline.

Runs a dedicated LLM pass asking a reviewer model to find cross-section
claim conflicts, then has the writer revise the draft with specific
corrections. Catches contradictions (e.g. qwen3:30b's tendency to
contradict its own earlier section) at generation time instead of
rejecting the whole draft at the downstream QA stage.

Ports the block at content_router_service.py stage 2A.5 (line 2387).
Non-fatal: exceptions are logged and the pipeline continues with the
un-revised draft.

## Observation during Phase E migration

The legacy pipeline has TWO self-review code paths:

1. Inside ``_stage_generate_content`` (gated on ``enable_writer_self_review``)
2. Stage 2A.5 in the orchestrator (NOT gated — always runs)

When the flag is True, self-review runs twice. When False, it still
runs once (via #2). This is a pre-existing behavior bug but not one
we fix during migration — preserving observable behavior means
preserving this too. Filed as follow-up; for now this Stage is
behaviorally identical to the legacy #2 block.

## Context reads

- ``task_id`` (str), ``topic`` (str)
- ``title`` (str) — populated by generate_content stage
- ``content`` (str) — populated by generate_content stage

## Context writes

- ``content`` — revised if contradictions were fixed
- ``content_length`` — recalculated after revision
- Audit log entry ``writer_self_review_pass``
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class WriterSelfReviewStage:
    name = "writer_self_review"
    description = "LLM self-review pass to catch cross-section contradictions"
    timeout_seconds = 300
    # Legacy stage 2A.5 swallowed exceptions ("non-fatal").
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.audit_log import audit_log_bg
        from services.self_review import self_review_and_revise as _self_review_and_revise

        task_id = context.get("task_id")
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        title = context.get("title", "")

        if not content_text:
            # Nothing to review. Legacy stage would have just quietly
            # run against an empty string — make the skip explicit.
            return StageResult(
                ok=True,
                detail="no content to review",
                metrics={"skipped": True},
            )

        # Phase H step 5 (GH#95): site_config is seeded on the pipeline
        # context by content_router_service. Tests build context dicts
        # with the fake site_config wired in explicitly.
        _sc = context["site_config"]

        try:
            revised_text, stats = await _self_review_and_revise(
                content_text, title, topic, _sc,
            )
        except Exception as e:
            logger.warning("[SELF_REVIEW] Stage failed (non-fatal): %s", e)
            return StageResult(
                ok=False,
                detail=f"self-review raised {type(e).__name__}: {e}",
            )

        updates: dict[str, Any] = {}
        if stats.get("revised"):
            updates["content"] = revised_text
            updates["content_length"] = len(revised_text)

        try:
            audit_log_bg(
                "writer_self_review_pass", "content_router",
                stats,
                task_id=task_id,
            )
        except Exception as e:
            logger.debug("audit_log_bg failed: %s", e)

        return StageResult(
            ok=True,
            detail="revised" if stats.get("revised") else "no changes",
            context_updates=updates,
            metrics={
                "revised": bool(stats.get("revised")),
                "contradictions_found": int(stats.get("contradictions_found", 0)),
                "skipped": bool(stats.get("skipped", False)),
            },
        )
