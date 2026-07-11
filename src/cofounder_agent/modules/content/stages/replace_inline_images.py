"""ReplaceInlineImagesStage — legacy zombie stage (kept, not on any graph).

The #362 atom-cutover decomposed this stage into the ``content.plan_image_markers``
/ ``content.generate_images`` / ``content.inject_images`` atoms; no ``graph_def``
references ``stage.replace_inline_images`` any longer. The class survives only
because it is still registered in ``plugins/registry.py`` ``_SAMPLES`` (surfaced
as a virtual atom nobody schedules) and has dedicated ``execute()`` tests.
Retiring it (drop the class + the ``_SAMPLES`` row + the execute() tests) is the
follow-up to the 2026-07 image half of the atom-independence burn-down.

The inline-image helper web that this class delegates to moved to
``modules/content/atoms/_image_helpers.py`` (a ``_``-prefixed library, per the
atom-independence convention) so ``modules/content/image_helpers.py`` no longer
has to reach into a stage to re-export it. This module now imports those helpers
from their new home.
"""

from __future__ import annotations

import logging
from typing import Any

# Helper web now lives in the atoms/_image_helpers library (relocated 2026-07).
# Re-imported here so the class's calls resolve and the legacy
# ``from modules.content.stages.replace_inline_images import _X`` test paths keep
# working; helper-internal test patch targets (httpx / gpu / _try_image_gen /
# _resolve_gen_response …) moved to modules.content.atoms._image_helpers.
from modules.content.atoms._image_helpers import (
    _PLACEHOLDER_RE,
    _batch_generate_all_images,
    _cleanup_leaked_descriptions,
    _normalize_from_router,
    _plan_and_inject_placeholders,
    _resolve_one_placeholder,
)
from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class ReplaceInlineImagesStage:
    name = "replace_inline_images"
    description = "Decide + generate inline images (image-gen primary, Pexels fallback)"
    timeout_seconds = 300
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.image_service import get_image_service

        task_id = context.get("task_id")
        post_id = context.get("post_id")
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        database_service = context.get("database_service")
        category = context.get("category", "technology")
        # DI seam (glad-labs-stack#330) — content_router_service seeds
        # site_config into the stage context. Tests pass a mock SiteConfig.
        site_config = context.get("site_config")
        platform = context.get("platform")

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

        # Build the image service only after the cheap guards pass —
        # ``get_image_service`` requires a real ``site_config`` (#272
        # Phase-2e), seeded into the context by content_router_service.
        image_service = context.get("image_service") or get_image_service(
            site_config=site_config  # type: ignore[arg-type]
        )

        stages = context.setdefault("stages", {})
        updates: dict[str, Any] = {}

        # VRAM guard (glad-labs-stack 2026-05-19 jank-audit finding #4):
        # the writer LLM (~20 GB for gemma3:27b) stays resident from the
        # preceding LLM stages because Ollama's default keep_alive is 5
        # min. image-gen Lightning loads ~12 GB on top, which OOMs 24 GB
        # cards and runs at ~98% VRAM on a 32 GB card. The
        # gpu_scheduler already unloads on the image-gen lock acquire, but
        # keep_alive=0 is fire-and-forget — without the grace sleep an
        # immediate /generate (the inline-image prompt build a few
        # lines below) can re-load before the writer's VRAM has been
        # released. This helper makes the unload deterministic +
        # tunable per ``app_settings.pipeline_writer_unload_before_image_gen``.
        from services.llm_providers.ollama_unload import (
            maybe_unload_writer_before_image_gen,
        )
        await maybe_unload_writer_before_image_gen(
            site_config=site_config,
            stage_label=self.name,
        )

        # Look for existing placeholders from the writer; otherwise ask
        # the Image Decision Agent to plan + inject them.
        placeholders = _PLACEHOLDER_RE.findall(content_text)
        if not placeholders:
            content_text, plan = await _plan_and_inject_placeholders(
                content_text, topic, category, site_config=site_config,
            )
            if plan is not None and plan.get("featured_image_plan"):
                updates["featured_image_plan"] = plan["featured_image_plan"]
            if plan is not None and plan.get("agent_error"):
                stages["2c_image_agent_error"] = plan["agent_error"]
            placeholders = _PLACEHOLDER_RE.findall(content_text)

        if not placeholders:
            stages["2c_inline_images_replaced"] = False
            logger.info("No [IMAGE-N] placeholders to replace")
            updates["stages"] = stages
            return StageResult(
                ok=True,
                detail="no placeholders",
                context_updates=updates,
                metrics={"inline_images_replaced": 0},
            )

        logger.info(
            "STAGE 2C: Replacing %d inline image placeholders...",
            len(placeholders),
        )

        used_image_ids: set[str] = set()
        # poindexter#733 — batch GPU work into two phases:
        #   Phase 1: one Ollama lock → generate ALL prompts
        #   Phase 2: one image-gen lock → render ALL images
        # This eliminates N×2 GPU lock acquisitions (6 for 3 images) and
        # the Ollama↔image-gen model-swap churn that caused ~95 s avg stage time.
        image_gen_urls = await _batch_generate_all_images(
            placeholders=placeholders,
            topic=topic,
            site_config=site_config,
            task_id=task_id,
            platform=platform,
        )
        for (num, desc), img_url in zip(placeholders, image_gen_urls, strict=False):
            content_text = await _resolve_one_placeholder(
                num=num,
                desc=desc,
                topic=topic,
                content_text=content_text,
                image_service=image_service,
                used_image_ids=used_image_ids,
                site_config=site_config,
                task_id=task_id,
                post_id=post_id,
                platform=platform,
                pregenerated_image_url=img_url,
            )

        content_text = _cleanup_leaked_descriptions(content_text)
        content_text = _normalize_from_router(content_text)

        # Persist the image-populated content.
        await database_service.update_task(
            task_id=task_id, updates={"content": content_text},
        )

        stages["2c_inline_images_replaced"] = True
        updates.update({
            "content": content_text,
            "inline_images_replaced": len(used_image_ids),
            "stages": stages,
        })
        logger.info("Replaced %d inline images in content", len(used_image_ids))

        return StageResult(
            ok=True,
            detail=f"{len(used_image_ids)} images replaced",
            context_updates=updates,
            metrics={"inline_images_replaced": len(used_image_ids)},
        )
