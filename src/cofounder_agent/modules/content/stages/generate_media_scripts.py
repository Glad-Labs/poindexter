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

        # Bug C2: prefer a clean title for narration. The ``title`` channel
        # can carry a style-rubric line leaked by content.generate_title; the
        # podcast intro ("Today's episode: …") + video voiceover must speak the
        # real title, which lives in seo_title / the content H1.
        title = _resolve_media_title(context)
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

        # Per-step model pin — media scripts use video_scene_model, falling
        # back to default_ollama_model. "auto"/unset is treated as "no model"
        # → skip the (non-critical) stage. The cost_tier.standard.model
        # fallback was removed.
        model = (
            (sc.get("video_scene_model") if sc is not None else None)
            or (sc.get("default_ollama_model") if sc is not None else None)
        )
        if not model or model == "auto":
            logger.debug(
                "generate_media_scripts: no media-script model configured "
                "(video_scene_model / default_ollama_model unset or 'auto') "
                "— stage skipped",
            )
            return StageResult(
                ok=True,
                detail="no media-script model configured — stage skipped",
                metrics={"skipped": True},
            )
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
        # Long-form VIDEO narration script (poindexter#689) — declared before the
        # try so a later scene-parse failure preserves it, same as the audio paths.
        video_long_script = ""

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

            # Long-form VIDEO narration script (poindexter#689) — distinct from
            # the podcast script, paced to on-screen visuals; its CTA is appended
            # at render time. Guarded + fail-soft like the scene call below.
            if pool is not None and platform is not None:
                try:
                    async with gpu.lock(
                        "ollama", model=model,
                        task_id=context.get("task_id"), phase="media_scripts",
                    ):
                        vn_result = await platform.dispatch.complete(
                            pool=pool,
                            messages=[{"role": "user", "content": _build_video_narration_prompt(
                                title, clean_content,
                            )}],
                            model=model,
                            tier="standard",
                            timeout_s=120,
                            temperature=0.6,
                            max_tokens=2048,
                        )
                    vn_text = (getattr(vn_result, "text", "") or "").strip()
                    video_long_script = (
                        _normalize_for_speech(vn_text, site_config=sc) if vn_text else ""
                    )
                    if video_long_script:
                        logger.info("[MEDIA] Video narration script: %d chars", len(video_long_script))
                except Exception as vn_exc:
                    logger.warning("[MEDIA] video narration script failed: %s", vn_exc)

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
                    "video_long_script": video_long_script,
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
                    "video_long_script": video_long_script,
                    "stages": stages,
                },
            )


def _first_h1(content: str) -> str:
    """Return the text of the first Markdown H1 (``# ...``), or '' if none."""
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _resolve_media_title(context: dict[str, Any]) -> str:
    """Resolve the reader-facing title for media narration (Bug C2).

    The ``title`` channel can carry a polluted value: a style-rubric line
    leaked by content.generate_title lands in ``pipeline_versions.title`` even
    when the published post recovers a clean title from its content H1. Media
    narration (the podcast intro "Today's episode: …" and the video voiceover)
    must speak the real title, so prefer the clean sources — ``seo_title``,
    then the content H1 — falling back to the raw ``title`` only as a last
    resort.
    """
    seo_title = (context.get("seo_title") or "").strip()
    if seo_title:
        return seo_title
    h1 = _first_h1(context.get("content") or "")
    if h1:
        return h1
    return (context.get("title") or "").strip()


# Long-form video narration prompt. DB-configurable via UnifiedPromptManager
# (key ``video.long_form_narration`` in skills/content/video/SKILL.md; a
# Langfuse / prompt-store override wins). This module-level fallback mirrors
# the SKILL.md default so tests + bootstrap resolve without a prompt store.
#
# Bug A: the renderer pairs narration with generic static imagery, so the
# script must read as standalone audio and must NEVER direct the viewer's eye
# ("on screen", "here we see", "watch as") — those promise visuals the footage
# cannot deliver, which a viewer immediately notices.
_VIDEO_NARRATION_FALLBACK = (
    "Write a voiceover narration script for a long-form video about the "
    "article below.\n\n"
    "The narration is spoken aloud and must stand on its own as audio. Write "
    "it for the ear: explain the subject directly to the listener. Do not "
    "refer to any accompanying imagery — the supporting footage is generic and "
    "will not match specific visual references, so keep every line meaningful "
    "with the eyes closed.\n"
    "- Tighter and more focused than an audio-only podcast; no 'welcome back' "
    "radio filler.\n"
    "- Open with a brief hook, walk the key points in order, then a natural "
    "closing line. Do NOT add a like/subscribe call-to-action — that is "
    "appended separately.\n"
    "- Plain spoken prose. No headings, no stage directions.\n\n"
    "TITLE: {title}\n\n"
    "ARTICLE:\n{content}\n\n"
    "NARRATION:"
)


def _build_video_narration_prompt(title: str, clean_content: str) -> str:
    """Prompt for the long-form VIDEO narration script.

    Pure spoken narration (Bug A) — the renderer shows generic static imagery,
    so the script never references on-screen visuals. Operator-tunable via
    UnifiedPromptManager (``video.long_form_narration``); the module-level
    fallback mirrors the SKILL.md default for tests / bootstrap.
    """
    content = clean_content[:3500]
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            "video.long_form_narration", title=title, content=content,
        )
    except Exception:  # noqa: BLE001 — prompt resolution is best-effort
        return _VIDEO_NARRATION_FALLBACK.format(title=title, content=content)


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


# Tolerant SHORT: marker (Glad-Labs/poindexter#689). The original
# ``SHORT:\s*\n`` required the marker to sit ALONE on its line, so the Shorts
# narration was never extracted: the local phi4:14b writer puts the narration
# INLINE after the marker (``SHORT: Ever wondered...``) — or decorates it
# (``**SHORT:**``, ``PART 2 - SHORT:``). On 100% of prod runs the split
# returned 1 part and ``short_summary`` fell back to "", so the video director
# never built a short_shot_list and ``render_short_video`` no-opped. Same
# tolerant-parse philosophy as the #1445 director reconcile: meet the model's
# real output rather than reject it.
#
# Matches a ``SHORT:`` marker at the start of any line, allowing (all optional):
#   - a ``PART 2`` lead-in with a ``-`` / ``--`` / ``:`` separator,
#   - markdown decoration around the word (``**SHORT:**``, ``## SHORT:``),
#   - whitespace before the colon.
# Whatever follows on the SAME or NEXT line is captured as the narration
# (``re.split`` drops the matched marker; the caller ``.strip()``s the rest).
_SHORT_SPLIT = re.compile(
    r'(?:^|\n)[^\S\n]*'                       # line start + optional indent
    r'(?:part[^\S\n]*2[^\S\n]*[-–—:]*[^\S\n]*)?'  # optional "PART 2" lead-in
    r'[#>*_]*[^\S\n]*'                        # optional markdown decoration
    r'short[*_]*[^\S\n]*:[*_]*[ \t]*',        # SHORT, optional bold, colon, trailing hspace
    re.IGNORECASE,
)

# A numbered scene line, e.g. ``3. ...`` / ``3) ...`` / ``3: ...`` / ``3- ...``.
_SCENE_LINE = re.compile(r'^[^\S\n]*\d+[.):\-]')

# Paragraph break: a blank line (optionally containing horizontal whitespace).
_PARA_BREAK = re.compile(r'\n[^\S\n]*\n')


def _extract_scene_lines(scenes_raw: str) -> list[str]:
    """Strip numbering/quotes off each line and keep the substantive ones."""
    scenes: list[str] = []
    for line in scenes_raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^\d+[.):\-]\s*", "", line).strip().strip('"')
        if len(cleaned) > 20:
            scenes.append(cleaned)
    return scenes


def _fallback_split_trailing_prose(scene_output: str) -> tuple[str, str]:
    """No explicit ``SHORT:`` marker — recover the narration the model wrote
    without the label by peeling off the trailing prose paragraph.

    Returns ``(scenes_raw, short_raw)``. Only fires when the output has a blank
    line AND the trailing block is prose (no numbered scene lines); otherwise
    returns ``(scene_output, "")`` so an all-scenes output keeps an empty
    short_summary rather than fabricating one from a scene line.
    """
    blocks = _PARA_BREAK.split(scene_output.strip())
    if len(blocks) < 2:
        return scene_output, ""
    last = blocks[-1].strip()
    last_lines = [ln for ln in last.split("\n") if ln.strip()]
    if not last_lines or any(_SCENE_LINE.match(ln) for ln in last_lines):
        return scene_output, ""
    return "\n\n".join(blocks[:-1]), last


def _parse_scene_output(
    scene_output: str,
    normalize_for_speech: Any,
) -> tuple[list[str], str]:
    """Split the LLM output into (video_scenes, short_summary)."""
    parts = _SHORT_SPLIT.split(scene_output, maxsplit=1)
    if len(parts) >= 2:
        scenes_raw, short_raw = parts[0], parts[1].strip()
    else:
        scenes_raw, short_raw = _fallback_split_trailing_prose(scene_output)
    short_summary = normalize_for_speech(short_raw) if short_raw else ""
    return _extract_scene_lines(scenes_raw), short_summary
