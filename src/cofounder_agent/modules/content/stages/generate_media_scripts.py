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
from services.audio_gen_service import generate_audio, is_audio_gen_enabled
from services.tts_service import is_tts_enabled, synthesize_speech

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

        # Resolve the asyncpg pool from the database_service in context —
        # stages run inside the pipeline runner which seeds this for us.
        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None

        # poindexter#716 — route through the cost-tier resolver so that
        # "auto" / missing configs follow operator settings instead of
        # silently pinning "llama3:latest" (dangle-after-upgrade footgun).
        _configured_model = (
            (sc.get("video_scene_model") if sc is not None else None)
            or (sc.get("default_ollama_model") if sc is not None else None)
        )
        if not _configured_model or _configured_model == "auto":
            if pool is not None:
                from services.llm_providers.dispatcher import resolve_tier_model
                try:
                    model = await resolve_tier_model(pool, "standard")
                except Exception as _exc:
                    logger.warning(
                        "generate_media_scripts: resolve_tier_model failed (%s); "
                        "stage will be skipped",
                        _exc,
                    )
                    return StageResult(
                        ok=True,
                        detail=f"model resolution failed: {_exc}",
                        metrics={"skipped": True},
                    )
            else:
                # No pool in tests/bootstrap — skip rather than hardcode.
                logger.debug(
                    "generate_media_scripts: no DB pool and no explicit model; "
                    "skipping stage",
                )
                return StageResult(
                    ok=True,
                    detail="no DB pool for tier resolution and no explicit model configured",
                    metrics={"skipped": True},
                )
        else:
            model = _configured_model
        # Seam 1 Wave 3d (#667): LLM completions go through the capability
        # handle. This non-critical stage already degrades without a DB pool;
        # a missing handle degrades the same way (logged, never silent).
        platform = context.get("platform")

        clean_content = _strip_markdown(content_text)

        podcast_script = ""
        video_scenes: list[str] = []
        short_summary = ""
        # Declared before the try so a later scene-parse failure can still
        # preserve audio built upstream (podcast TTS + intro sting run before
        # the video-scenes call). poindexter#690 — these were direct
        # context[...] writes (dropped by make_stage_node) + undeclared
        # PipelineState channels; now flow out via context_updates.
        podcast_audio_path = ""
        podcast_intro_audio_path = ""

        try:
            # Call 1: Podcast script (reuses podcast_service's proven approach).
            async with gpu.lock("ollama", model=model, task_id=context.get("task_id"), phase="media_scripts"):
                podcast_script = await _build_script_with_llm(
                    title, content_text, site_config=sc,
                )

            if podcast_script and len(podcast_script) > 200:
                logger.info("[MEDIA] Podcast script: %d chars", len(podcast_script))
            else:
                logger.warning(
                    "[MEDIA] Podcast script too short (%d chars)",
                    len(podcast_script or ""),
                )
                podcast_script = ""

            # TTS narration — synthesize the podcast script to audio via Speaches.
            # Non-critical: failure logs a warning, pipeline continues.
            # Enable with app_settings: podcast_tts_enabled=true.
            if podcast_script and is_tts_enabled(sc):
                try:
                    import os
                    import tempfile
                    suffix = (sc.get("podcast_tts_format", "wav") if sc else "wav") or "wav"
                    with tempfile.NamedTemporaryFile(
                        suffix=f".{suffix}", delete=False,
                    ) as tmp:
                        tts_path = tmp.name
                    audio_bytes = await synthesize_speech(
                        podcast_script,
                        site_config=sc,
                        output_path=tts_path,
                    )
                    if audio_bytes:
                        podcast_audio_path = tts_path
                        logger.info(
                            "[MEDIA] Podcast TTS audio: %d bytes → %s",
                            len(audio_bytes), tts_path,
                        )
                    else:
                        os.unlink(tts_path)
                except Exception as tts_exc:
                    logger.warning("[MEDIA] podcast TTS failed: %s", tts_exc)

            # Audio gen — podcast intro sting via StableAudioOpen.
            # Non-critical, default-off (audio_gen_engine='' by default).
            # Activate: set audio_gen_engine=stable-audio-open-1.0 in app_settings.
            if podcast_script and is_audio_gen_enabled(sc):
                try:
                    intro_result = await generate_audio(
                        f"podcast intro sting for: {title}",
                        "intro",
                        site_config=sc,
                    )
                    if intro_result is not None:
                        path = intro_result.file_path or ""
                        if path:
                            podcast_intro_audio_path = path
                            logger.info("[MEDIA] Podcast intro sting: %s", path)
                except Exception as sfx_exc:
                    logger.warning("[MEDIA] audio_gen intro sting failed: %s", sfx_exc)

            # Call 2: Video scenes + short summary (single LLM call).
            scene_prompt = _build_scene_prompt(
                title, clean_content,
                sc.get("site_name", "our site") if sc is not None else "our site",
            )

            scene_output = ""
            if pool is None or platform is None:
                # Tests / bootstrap path — skip the LLM call gracefully.
                # The stage is marked non-critical (halts_on_failure=False),
                # so an empty scenes payload is fine for non-prod runs. A
                # missing Platform handle (no kernel access) degrades the same.
                logger.warning(
                    "[MEDIA] no DB pool / Platform handle in context — "
                    "skipping video-scenes LLM call",
                )
            else:
                async with gpu.lock(
                    "ollama", model=model,
                    task_id=context.get("task_id"), phase="media_scripts",
                ):
                    result = await platform.dispatch.complete(
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
                    scene_output,
                    # Bind the run's site_config: _normalize_for_speech
                    # requires it (#272) but _parse_scene_output invokes the
                    # normalizer positionally. Passing it bare raised
                    # "podcast_service requires a site_config", aborting the
                    # stage and starving the video director (0 shot lists).
                    lambda t: _normalize_for_speech(t, site_config=sc),
                )
                logger.info(
                    "[MEDIA] Video scenes: %d, Short summary: %d chars",
                    len(video_scenes), len(short_summary),
                )

            # Audio gen — ambient video bed via StableAudioOpen.
            ambient_audio_path = ""
            if video_scenes and is_audio_gen_enabled(sc):
                try:
                    ambient_prompt = video_scenes[0][:200] if video_scenes else title
                    ambient_result = await generate_audio(
                        ambient_prompt,
                        "ambient",
                        site_config=sc,
                    )
                    if ambient_result is not None:
                        path = ambient_result.file_path or ""
                        if path:
                            ambient_audio_path = path
                            logger.info("[MEDIA] Video ambient bed: %s", path)
                except Exception as sfx_exc:
                    logger.warning("[MEDIA] audio_gen ambient bed failed: %s", sfx_exc)

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
                    "video_ambient_audio_path": ambient_audio_path,
                    "podcast_audio_path": podcast_audio_path,
                    "podcast_intro_audio_path": podcast_intro_audio_path,
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
            # Preserve any podcast_script built before the failure. The video
            # director only needs the script, so a later scene-parsing error
            # must NOT discard it — otherwise the director starves and produces
            # no shot list. Earlier behavior dropped it (root cause of 0 shot
            # lists alongside the #272 normalizer bug above).
            return StageResult(
                ok=bool(podcast_script),
                detail=(
                    f"{type(e).__name__}: {e} "
                    f"(podcast_script={len(podcast_script)}c preserved)"
                ),
                context_updates={
                    "podcast_script": podcast_script,
                    "podcast_script_length": len(podcast_script),
                    # Preserve audio built before the failure (poindexter#690),
                    # same contract as podcast_script above.
                    "podcast_audio_path": podcast_audio_path,
                    "podcast_intro_audio_path": podcast_intro_audio_path,
                    "stages": stages,
                },
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
