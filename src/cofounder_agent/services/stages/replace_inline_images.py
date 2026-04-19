"""ReplaceInlineImagesStage — stage 2C of the content pipeline.

Wraps the legacy ``_stage_replace_inline_images`` in content_router_service.py
via a thin Stage-adapter shim. The legacy function is ~250 lines of
intricate GPU-lock/SDXL/Pexels/R2-upload logic that isn't trivial to
port cleanly in one commit — so this stage is intentionally a thin
adapter that hands the current context off to the existing implementation.

Detangling the legacy body into its own files (prompt builder, SDXL
caller, Pexels fallback, R2 upload, regex scrubbers) is tracked as a
Phase E follow-up. Shipping the wrapper now means the full stage list
is registered and the pipeline cutover can happen as soon as every
other stage is wrapper-or-full.

## Context reads

- ``task_id`` (str), ``topic`` (str), ``content`` (str)
- ``database_service``
- ``image_service`` (optional — lazy-gets one if not in context)
- ``category`` (str, passed through result-dict adapter)

## Context writes

- ``content`` (possibly modified with inline image tags)
- ``featured_image_plan`` (if the decision agent set one)
- ``inline_images_replaced`` (int)
- ``stages["2c_inline_images_replaced"]`` (bool)
- ``image_style`` (str, if legacy populated it)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class ReplaceInlineImagesStage:
    name = "replace_inline_images"
    description = "Decide + generate inline images (SDXL primary, Pexels fallback)"
    # Legacy _get_stage_timeout returned 300s for this stage.
    timeout_seconds = 300
    # Legacy did not halt the orchestrator; any failure inside was
    # either logged or recovered-from silently.
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.content_router_service import _stage_replace_inline_images
        from services.image_service import get_image_service

        task_id = context.get("task_id")
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        database_service = context.get("database_service")
        image_service = context.get("image_service") or get_image_service()

        if not content_text:
            return StageResult(
                ok=True,
                detail="no content to process",
                metrics={"skipped": True},
            )
        if not task_id or database_service is None:
            return StageResult(
                ok=False,
                detail="context missing task_id or database_service",
            )

        # The legacy function reads + writes a shared dict it calls
        # `result`. Adapt our context to that shape: copy the legacy-
        # expected keys in, run it, then copy the updated keys back onto
        # the Stage's context_updates. The dict is passed by reference
        # so the legacy function's mutations are visible here.
        result_dict: dict[str, Any] = {
            "category": context.get("category", "technology"),
            "stages": dict(context.get("stages", {})),
        }

        try:
            new_content = await _stage_replace_inline_images(
                database_service,
                task_id,
                topic,
                content_text,
                image_service,
                result_dict,
            )
        except Exception as e:  # noqa: BLE001 — legacy swallowed errors
            logger.warning("replace_inline_images raised: %s", e)
            return StageResult(
                ok=False,
                detail=f"legacy raised: {e}",
            )

        # Collect the interesting keys the legacy function set.
        updates: dict[str, Any] = {"stages": result_dict["stages"]}
        if new_content is not None:
            updates["content"] = new_content
        if "featured_image_plan" in result_dict:
            updates["featured_image_plan"] = result_dict["featured_image_plan"]
        if "inline_images_replaced" in result_dict:
            updates["inline_images_replaced"] = result_dict["inline_images_replaced"]
        if "image_style" in result_dict:
            updates["image_style"] = result_dict["image_style"]

        return StageResult(
            ok=True,
            detail=f"{updates.get('inline_images_replaced', 0)} images replaced",
            context_updates=updates,
            metrics={
                "inline_images_replaced": int(updates.get("inline_images_replaced", 0)),
            },
        )
