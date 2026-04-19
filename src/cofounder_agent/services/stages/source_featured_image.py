"""SourceFeaturedImageStage — stage 3 of the content pipeline.

Thin wrapper around the legacy ``_stage_source_featured_image`` in
content_router_service.py — same rationale as ReplaceInlineImagesStage:
the legacy body is ~230 lines of SDXL-prompt-building, style rotation,
GPU locks, and Pexels fallback. Porting it in one commit risks
behavioral regressions; wrapping it now unblocks the full Stage
registration / cutover while the detangle happens as a follow-up.

## Context reads

- ``topic`` (str), ``tags`` (list[str])
- ``generate_featured_image`` (bool — defaults True)
- ``task_id`` (str)
- ``image_service`` (optional — lazy-gets one)

## Context writes

- ``featured_image`` (the object the legacy function returned)
- ``featured_image_url``, ``featured_image_alt``, ``featured_image_width``,
  ``featured_image_height``, ``featured_image_photographer``,
  ``featured_image_source`` (copied from the result dict the legacy
  function populates)
- ``stages["3_featured_image_found"]`` (bool)
- ``image_style`` (if set by the rotation logic)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class SourceFeaturedImageStage:
    name = "source_featured_image"
    description = "Source a featured image — SDXL primary, Pexels fallback"
    timeout_seconds = 300
    halts_on_failure = False  # Legacy: featured image failure is non-fatal.

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.content_router_service import _stage_source_featured_image
        from services.image_service import get_image_service

        topic = context.get("topic", "")
        tags = context.get("tags") or []
        generate_featured_image = bool(context.get("generate_featured_image", True))
        task_id = context.get("task_id")
        image_service = context.get("image_service") or get_image_service()

        # Legacy stage populates these keys on the shared result dict
        # and the orchestrator reads them afterward. Adapt via a local
        # dict the legacy function can mutate.
        result_dict: dict[str, Any] = {
            "stages": dict(context.get("stages", {})),
            # Legacy stage reads featured_image_prompt for reuse
            "featured_image_prompt": context.get("featured_image_prompt", ""),
        }

        try:
            featured_image = await _stage_source_featured_image(
                topic, tags, generate_featured_image, image_service,
                result_dict, task_id=task_id,
            )
        except Exception as e:  # noqa: BLE001 — non-fatal in legacy
            logger.warning("source_featured_image raised: %s", e)
            return StageResult(
                ok=False,
                detail=f"legacy raised: {e}",
            )

        updates: dict[str, Any] = {
            "featured_image": featured_image,
            "stages": result_dict["stages"],
        }
        # Propagate every featured_image_* key the legacy function writes.
        for key in (
            "featured_image_url",
            "featured_image_alt",
            "featured_image_width",
            "featured_image_height",
            "featured_image_photographer",
            "featured_image_source",
            "image_style",
        ):
            if key in result_dict:
                updates[key] = result_dict[key]

        return StageResult(
            ok=True,
            detail=(
                "image sourced"
                if featured_image else "no image (disabled or all strategies failed)"
            ),
            context_updates=updates,
            metrics={"has_featured_image": featured_image is not None},
        )
