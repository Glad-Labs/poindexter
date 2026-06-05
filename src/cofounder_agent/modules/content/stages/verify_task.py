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
- ``preview_token`` / ``preview_url`` (str) — minted here so the rendered-
  preview QA rail (``qa.vision``) has a URL to screenshot. The qa.* block runs
  BEFORE ``finalize_task``, which is where the token used to be minted, so
  generating it at the top of the pipeline is what lets the gate run at all
  (Glad-Labs/poindexter#563). ``finalize_task`` reuses this token.

Phase E migration notes:
- Replaces ``_stage_verify_task`` in services/content_router_service.py
- Preserves exact observable behavior (log messages, result-dict keys)
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)

_DEFAULT_PREVIEW_BASE_URL = "http://localhost:8002"


def _mint_preview(context: dict[str, Any]) -> dict[str, Any]:
    """Mint a preview token + URL early so the qa.vision rail can screenshot
    the rendered page. Returns the two channels for ``context_updates``.

    Reuses an existing ``preview_token`` if a caller already seeded one (so a
    retry / replay keeps a stable URL). The base URL comes from the kernel via
    the capability handle (``platform.config.get("preview_base_url")``, Seam 1
    Wave 3e #667) so the screenshot hits an address reachable from inside the
    worker container. None-tolerant: a missing handle (tests / ad-hoc CLI) falls
    back to the default, exactly as the prior ``site_config``-None seam did.
    """
    token = (context.get("preview_token") or "").strip() or secrets.token_hex(16)

    base = _DEFAULT_PREVIEW_BASE_URL
    platform = context.get("platform")
    if platform is not None:
        try:
            base = platform.config.get("preview_base_url", _DEFAULT_PREVIEW_BASE_URL)
        except Exception:  # noqa: BLE001
            base = _DEFAULT_PREVIEW_BASE_URL
    return {
        "preview_token": token,
        "preview_url": f"{str(base).rstrip('/')}/preview/{token}",
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
            # Mint the preview token/URL early so the qa.vision rendered-preview
            # rail (which runs before finalize_task) has a URL to screenshot
            # (Glad-Labs/poindexter#563). Best-effort: a failure here must not
            # halt the pipeline, so the screenshot gate just stays skipped.
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
