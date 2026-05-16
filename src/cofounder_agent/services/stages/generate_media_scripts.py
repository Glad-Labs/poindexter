"""GenerateMediaScriptsStage — stage 4B of the content pipeline.

Generates podcast script + video scenes + short summary from the draft.
Ports ``_stage_generate_media_scripts`` in full.

Non-critical: any failure logs a warning and the pipeline continues.
Two separate LLM calls for reliability (legacy trade-off).

## Context reads

- ``task_id`` (str), ``title`` (str), ``content`` (str)

## Context writes

- ``podcast_script`` (str)
- ``video_scenes`` (list[str])
- ``short_summary_script`` (str)
- ``podcast_script_length``, ``video_scenes_count``, ``short_summary_length``
- ``stages["4b_media_scripts"]`` (bool)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class GenerateMediaScriptsStage:
    name = "generate_media_scripts"
    description = "Generate podcast script, video scenes, and short summary"
    # Two LLM calls, each up to 120s. Budget 300 for slow disks.
    timeout_seconds = 300
    halts_on_failure = False  # Legacy marked this "non-critical".

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.gpu_scheduler import gpu
        from services.podcast_service import (
            _build_script_with_llm,
            _normalize_for_speech,
            _strip_markdown,
        )

        title = context.get("title", "")
        content_text = context.get("content", "")

        if not content_text or not title:
            return StageResult(
                ok=True,
                detail="nothing to script (missing content or title)",
                metrics={"skipped": True},
            )

        logger.info("STAGE 4B: Generating media scripts (podcast + video scenes)...")

        # DI seam (glad-labs-stack#330) — stages read site_config from
        # context per content_router_service.process_content_generation_task.
        sc = context.get("site_config")
        model = (
            (sc.get("video_scene_model") if sc is not None else None)
            or (sc.get("default_ollama_model") if sc is not None else None)
            or "llama3:latest"
        )
        if model == "auto":
            model = "llama3:latest"

        # Resolve the asyncpg pool from the database_service in context —
        # stages run inside the pipeline runner which seeds this for us.
        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None

        clean_content = _strip_markdown(content_text)

        podcast_script = ""
        video_scenes: list[str] = []
        short_summary = ""

        try:
            # Call 1: Podcast script (reuses podcast_service's proven approach).
            async with gpu.lock("ollama", model=model, task_id=context.get("task_id"), phase="media_scripts"):
                podcast_script = await _build_script_with_llm(title, content_text)

            if podcast_script and len(podcast_script) > 200:
                logger.info("[MEDIA] Podcast script: %d chars", len(podcast_script))
            else:
                logger.warning(
                    "[MEDIA] Podcast script too short (%d chars)",
                    len(podcast_script or ""),
                )
                podcast_script = ""

            # Call 2: Video scenes + short summary (single LLM call).
            scene_prompt = _build_scene_prompt(
                title, clean_content,
                sc.get("site_name", "our site") if sc is not None else "our site",
            )

            scene_output = ""
            if pool is None:
                # Tests / bootstrap path — skip the LLM call gracefully.
                # The stage is marked non-critical (halts_on_failure=False),
                # so an empty scenes payload is fine for non-prod runs.
                logger.warning(
                    "[MEDIA] no DB pool in context — skipping video-scenes LLM call",
                )
            else:
                from services.llm_providers.dispatcher import dispatch_complete

                async with gpu.lock(
                    "ollama", model=model,
                    task_id=context.get("task_id"), phase="media_scripts",
                ):
                    result = await dispatch_complete(
                        pool=pool,
                        messages=[{"role": "user", "content": scene_prompt}],
                        model=model,
                        tier="standard",
                        timeout_s=120,
                        temperature=0.7,
                        max_tokens=2048,
                    )
                    scene_output = (getattr(result, "text", "") or "").strip()

            if scene_output:
                video_scenes, short_summary = _parse_scene_output(
                    scene_output, _normalize_for_speech,
                )
                logger.info(
                    "[MEDIA] Video scenes: %d, Short summary: %d chars",
                    len(video_scenes), len(short_summary),
                )

            stages = context.setdefault("stages", {})
            stages["4b_media_scripts"] = True

            logger.info(
                "[MEDIA] Generated podcast script (%d chars) + %d video scenes for '%s'",
                len(podcast_script), len(video_scenes), title[:50],
            )

            return StageResult(
                ok=True,
                detail=f"podcast={len(podcast_script)}c scenes={len(video_scenes)}",
                context_updates={
                    "podcast_script": podcast_script,
                    "video_scenes": video_scenes,
                    "short_summary_script": short_summary,
                    "podcast_script_length": len(podcast_script),
                    "video_scenes_count": len(video_scenes),
                    "short_summary_length": len(short_summary),
                    "stages": stages,
                },
                metrics={
                    "podcast_script_length": len(podcast_script),
                    "video_scenes_count": len(video_scenes),
                    "short_summary_length": len(short_summary),
                },
            )
        except Exception as e:
            logger.warning("[MEDIA] Script generation failed (non-fatal): %s", e)
            stages = context.setdefault("stages", {})
            stages["4b_media_scripts"] = False
            return StageResult(
                ok=False,
                detail=f"{type(e).__name__}: {e}",
                context_updates={"stages": stages},
            )


def _build_scene_prompt(title: str, clean_content: str, site_name: str) -> str:
    """Build the prompt for the video-scenes + short-summary LLM call."""
    return (
        "Generate TWO things for a blog post video:\n\n"
        "PART 1 — Write 6-8 numbered lines, each describing a photorealistic image "
        "for a video slideshow about this article. Each line is a Stable Diffusion XL prompt. "
        "Requirements: cinematic lighting, no people, no text, no faces, no hands, 4K quality. "
        "One scene per line.\n\n"
        "PART 2 — After a blank line, write \"SHORT:\" on its own line, then write a 60-second "
        "narration (about 150 words) summarizing the article for TikTok/YouTube Shorts. "
        f"Start with a hook, cover 2-3 key takeaways, end with \"Full article at {site_name}.\"\n\n"
        f"ARTICLE: {title}\n\n"
        f"{clean_content[:3000]}\n\n"
        "SCENES:"
    )


_SHORT_SPLIT = re.compile(r'(?:^|\n)\s*SHORT:\s*\n', re.IGNORECASE)


def _parse_scene_output(
    scene_output: str,
    normalize_for_speech: Any,
) -> tuple[list[str], str]:
    """Split the LLM output into (video_scenes, short_summary)."""
    parts = _SHORT_SPLIT.split(scene_output, maxsplit=1)
    scenes_raw = parts[0].strip()
    short_summary = (
        normalize_for_speech(parts[1].strip()) if len(parts) >= 2 else ""
    )
    scenes: list[str] = []
    for line in scenes_raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^\d+[.):\-]\s*", "", line).strip().strip('"')
        if len(cleaned) > 20:
            scenes.append(cleaned)
    return scenes, short_summary
