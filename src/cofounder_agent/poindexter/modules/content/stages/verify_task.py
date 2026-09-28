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
- ``preview_token`` / ``preview_url`` (str) — minted at the top of the
  pipeline (Glad-Labs/poindexter#563) so the token is stable for the whole run:
  ``content.compile_meta`` / ``finalize_task`` reuse it rather than minting a
  second one. ``preview_url`` is the OPERATOR's link
  (``services.preview_links``), the one approval-gate artifacts surface. The
  ``qa.vision`` rendered-preview leg does not fetch it: at QA time the draft is
  not persisted yet, so the leg renders the draft in-process instead.

Phase E migration notes:
- Replaces ``_stage_verify_task`` in services/content_router_service.py
- Preserves exact observable behavior (log messages, result-dict keys)
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from poindexter.plugins.stage import StageResult

logger = logging.getLogger(__name__)


def _mint_preview(context: dict[str, Any]) -> dict[str, Any]:
    """Mint the run's preview token and the operator's link to it.

    Returns the two channels for ``context_updates``. Reuses an existing
    ``preview_token`` if a caller already seeded one, so a retry or replay
    keeps a stable link. The link is built by ``services.preview_links`` from
    the capability handle's config (``platform.config``, Seam 1 Wave 3e #667):
    ``preview_base_url``, else ``http://{operator_service_host}:8002``. It is
    for the operator's device, not for anything inside the stack. A missing
    handle (tests, ad-hoc CLI) derives the ``localhost`` default.
    """
    from poindexter.services.preview_links import operator_preview_url

    token = (context.get("preview_token") or "").strip() or secrets.token_hex(16)
    platform = context.get("platform")
    config = platform.config if platform is not None else None
    return {
        "preview_token": token,
        "preview_url": operator_preview_url(config, token),
    }


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
            updates: dict[str, Any] = {
                "content_task_id": task_id,
                "stages": stages,
            }
            # Mint the preview token early so every node shares one token for
            # the run, and the operator's link rides the preview_url channel
            # (Glad-Labs/poindexter#563). Best-effort: a failure here must not
            # halt the pipeline; compile_meta mints a token if none exists.
            try:
                updates.update(_mint_preview(context))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not mint preview token early: %s", exc)
            return StageResult(
                ok=True,
                detail="task verified",
                context_updates=updates,
            )

        logger.warning("Task %s not found - this should not happen", task_id)
        stages["1_content_task_created"] = False
        return StageResult(
            ok=False,
            detail="task_id not found in DB",
            context_updates={"stages": stages},
            continue_workflow=True,  # Legacy: log and continue.
        )
