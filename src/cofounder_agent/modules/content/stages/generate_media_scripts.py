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
import os
import re
from typing import Any

from modules.content.stages._media_gpu_skip import surface_media_gpu_busy_skip
from plugins.stage import StageResult
from services.audio_gen_service import generate_audio, is_audio_gen_enabled
from services.gpu_admission import GpuBusyError
from services.gpu_scheduler import media_wait_budget_s
from services.tts_service import is_tts_enabled, resolve_tts_format, synthesize_speech
from utils.findings import emit_finding

_INTRO_PROMPT_FALLBACK = (
    "Podcast intro theme: warm analog synth ident, clean electric piano "
    "arpeggio rising into a soft pad chord, confident and modern, 100 BPM, "
    "studio quality, tight clean ending. No vocals, no sound effects, no "
    "foley, no noise."
)

_AMBIENT_PROMPT_FALLBACK = (
    "Lo-fi chillhop instrumental background music for a technology explainer "
    "video: mellow electric piano chords, warm bass, soft drum loop, 80 BPM, "
    "relaxed and focused. No vocals, no sound effects, no foley. "
    "Mood cues: {mood}"
)


def _ambient_mood_cues(scenes: list[Any]) -> str:
    """First real scene description, reduced to plain mood text.

    Scene entries are SDXL *visual* prompts (and legacy rows carry markdown
    headers like ``### PART 1``). Fed verbatim to the music model they produce
    literal soundscapes — an office scene became engine-room hum — so the
    scene text is demoted to trailing mood cues inside a music-directed
    template, and markdown/header junk is stripped before use.
    """
    for entry in scenes or []:
        text = str(entry).strip()
        text = re.sub(r"^#+\s.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"[*#_`]+", " ", text)
        text = re.sub(r"^\s*Scene\s+\w+\s*[:.\-]\s*", "", text, flags=re.IGNORECASE)
        text = " ".join(text.split())
        if len(text) >= 20:
            return text[:160]
    return ""

logger = logging.getLogger(__name__)

# Narration pace estimate — mirrors generate_video_shot_list._WORDS_PER_SECOND
# (~150 WPM). Kept local (not cross-stage imported) so the short prompt's word
# target derives from the same second-target the shot-list clamp uses (#867).
_WORDS_PER_SECOND = 2.5


class GenerateMediaScriptsStage:
    name = "generate_media_scripts"
    description = "Generate podcast script, video scenes, and short summary"
    # Two LLM calls, each up to 120s. Budget 300 for slow disks.
    timeout_seconds = 300
    halts_on_failure = False  # Legacy marked this "non-critical".
    # Surfaced onto the virtual atom's contract (poindexter#983) so the
    # architect can SEE this is the script writer and chain it to the
    # renderers. Mirrors the docstring's Context reads/writes.
    atom_requires = ("content",)
    atom_produces = ("podcast_script", "video_scenes", "short_summary_script")

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.gpu_scheduler import gpu
        from services.podcast_service import (
            _build_script_with_llm,
            _normalize_for_script,
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

        # Video-only mode (media_regen / backfill_media_scripts): generate
        # ONLY the video narration text — skip the podcast LLM call and every
        # audio side effect (podcast TTS, intro sting, ambient bed). The
        # regen callers run against pieces whose podcast is already a
        # published artifact; regenerating its script would desync the frozen
        # text from the shipped audio, and synthesizing fresh audio is a side
        # effect they must not pay for. A first-class flag rather than
        # patching is_tts_enabled/is_audio_gen_enabled: those patches are
        # module-global, and a live pipeline run in the same worker process
        # would silently skip ITS podcast audio while a regen held them.
        video_only = bool(context.get("media_scripts_video_only"))

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
            # Skipped in video-only mode — the caller's podcast is a shipped
            # artifact and the writeback discards podcast keys anyway.
            if not video_only:
                async with gpu.lock(
                    "ollama", model=model, task_id=context.get("task_id"),
                    phase="media_scripts",
                    # poindexter#914 P2 group 2 — bounded wait. This stage is
                    # explicitly non-critical (see the handler below), so skipping
                    # beats queueing behind a ~230s render up to the 900s lock
                    # ceiling on an article that is otherwise finished.
                    max_wait_s=media_wait_budget_s(), priority="background",
                ):
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
                    import tempfile
                    suffix = resolve_tts_format(sc)
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
            #
            # CURATED-FIRST (operator feedback 2026-08-07): a real show has
            # ONE theme, not per-episode generated music. When the operator
            # pins ``podcast_sting_file_path`` the pipeline uses that file
            # verbatim and skips generation entirely (no GPU spend). The
            # generated path is the bootstrap/fallback — its prompt is now
            # MUSIC-directed via ``audio_gen_intro_prompt_template`` (the
            # old prompt fed the ARTICLE TITLE to the music model, which
            # dutifully tried to sound like "VRAM Poisoning"), and its
            # duration covers the mixer's intro/outro windows (the 5s
            # default left the 6s outro trimming past end-of-file).
            curated_sting = str(
                (sc.get("podcast_sting_file_path", "") if sc is not None else "")
                or ""
            ).strip()
            if curated_sting and os.path.exists(curated_sting):
                podcast_intro_audio_path = curated_sting
                logger.info(
                    "[MEDIA] Podcast sting: curated file %s (generation skipped)",
                    curated_sting,
                )
            elif podcast_script and is_audio_gen_enabled(sc):
                try:
                    prompt_template = str(
                        sc.get(
                            "audio_gen_intro_prompt_template",
                            _INTRO_PROMPT_FALLBACK,
                        )
                        if sc is not None else _INTRO_PROMPT_FALLBACK
                    )
                    try:
                        sting_duration = float(
                            sc.get("audio_gen_intro_duration_s", "9")
                            if sc is not None else 9
                        )
                    except (TypeError, ValueError):
                        sting_duration = 9.0
                    intro_result = await generate_audio(
                        prompt_template,
                        "intro",
                        site_config=sc,
                        duration_s=sting_duration,
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
            # One canonical target (silent-tail fix): video_long_target_seconds
            # drives the prompt's second/word ask, and the director's target-
            # duration estimate downstream reads THIS script's actual words — so
            # the voice and the visual plan can no longer disagree by minutes.
            long_target_s = sc.get_int("video_long_target_seconds", 180) if sc is not None else 180
            if pool is not None and platform is not None:
                try:
                    async with gpu.lock(
                        "ollama", model=model,
                        task_id=context.get("task_id"), phase="media_scripts",
                        max_wait_s=media_wait_budget_s(), priority="background",
                    ):
                        vn_result = await platform.dispatch.complete(
                            pool=pool,
                            messages=[{"role": "user", "content": _build_video_narration_prompt(
                                title, clean_content,
                                target_seconds=long_target_s,
                                target_words=round(long_target_s * _WORDS_PER_SECOND),
                            )}],
                            model=model,
                            tier="standard",
                            timeout_s=120,
                            temperature=0.6,
                            max_tokens=2048,
                        )
                    vn_text = (getattr(vn_result, "text", "") or "").strip()
                    video_long_script = (
                        _normalize_for_script(vn_text, site_config=sc) if vn_text else ""
                    )
                    if video_long_script:
                        logger.info("[MEDIA] Video narration script: %d chars", len(video_long_script))
                except GpuBusyError as busy:
                    # Ahead of the broad handler: contention is not a failure of
                    # the narration call, and this sub-step is already optional.
                    logger.info(
                        "[MEDIA] video narration skipped — GPU busy (%s)", busy.reason,
                    )
                    surface_media_gpu_busy_skip(
                        "media_scripts_video_narration", busy,
                        task_id=context.get("task_id"),
                    )
                except Exception as vn_exc:
                    logger.warning("[MEDIA] video narration script failed: %s", vn_exc)

            # Runaway-long cap (silent-tail fix): a model that ignores the
            # target can emit a podcast-length "video narration". Trim to the
            # last full sentence within video_long_max_seconds — the SAME
            # ceiling the director's target-duration estimate clamps to, so the
            # script and the visual plan can't disagree past it. Deterministic,
            # no LLM call, sentence-safe (mirrors the #867 short trim).
            if video_long_script:
                long_max_s = sc.get_int("video_long_max_seconds", 300) if sc is not None else 300
                max_long_words = round(long_max_s * _WORDS_PER_SECOND)
                video_long_script, long_orig_w, long_kept_w = _trim_to_word_budget(
                    video_long_script, max_long_words,
                )
                if long_kept_w < long_orig_w:
                    logger.info(
                        "[MEDIA] long narration script trimmed %d->%d words (max %d)",
                        long_orig_w, long_kept_w, max_long_words,
                    )
                    emit_finding(
                        source="media.generate_scripts",
                        kind="long_script_trimmed",
                        title=f"long narration script trimmed {long_orig_w}->{long_kept_w} words",
                        body=(
                            f"The long video narration for task {context.get('task_id')} ran "
                            f"{long_orig_w} words (~{long_orig_w / _WORDS_PER_SECOND:.0f}s), over the "
                            f"video_long_max_seconds budget of {long_max_s}s "
                            f"({max_long_words} words). Trimmed to the last full sentence "
                            "within budget so the video stays inside the renderer's "
                            "practical length. Advisory — raise video_long_target_seconds "
                            "or tighten the long_form_narration prompt if this is frequent."
                        ),
                        severity="info",
                        dedup_key=f"long_script_trimmed:{context.get('task_id')}",
                        extra={
                            "task_id": str(context.get("task_id") or ""),
                            "original_words": long_orig_w,
                            "trimmed_words": long_kept_w,
                            "max_words": max_long_words,
                        },
                    )

            # Call 2: Video scenes + short summary (single LLM call).
            short_target_s = sc.get_int("video_short_target_seconds", 45) if sc is not None else 45
            scene_prompt = _build_scene_prompt(
                title, clean_content,
                sc.get("site_name", "our site") if sc is not None else "our site",
                target_seconds=short_target_s,
                target_words=round(short_target_s * _WORDS_PER_SECOND),
            )

            scene_output = ""
            short_min_words = (
                sc.get_int("video_short_min_words", 25) if sc is not None else 25
            )
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
                # Validity-gated with ONE retry (2026-08-01): a model can emit
                # scenes and then a garbage "short" — a bare **END**, or meta
                # commentary — which used to freeze into task_metadata and get
                # TTS'd into the render forever (a frozen 1-word short produced
                # a 12s video over 3.8s of voice). A fresh sample usually
                # fixes a formatting fumble; scenes are kept from whichever
                # attempt produced them.
                for scene_attempt in range(2):
                    async with gpu.lock(
                        "ollama", model=model,
                        task_id=context.get("task_id"), phase="media_scripts",
                        max_wait_s=media_wait_budget_s(), priority="background",
                    ):
                        result = await platform.dispatch.complete(
                            pool=pool,
                            messages=[{"role": "user", "content": scene_prompt}],
                            model=model,
                            tier="standard",
                            timeout_s=120,
                            temperature=0.7 if scene_attempt == 0 else 0.85,
                            max_tokens=2048,
                        )
                        scene_output = (getattr(result, "text", "") or "").strip()
                    if not scene_output:
                        continue
                    attempt_scenes, attempt_short = _parse_scene_output(
                        scene_output,
                        # Bind the run's site_config: the normalizer
                        # requires it (#272) but _parse_scene_output invokes it
                        # positionally. Passing it bare raised
                        # "podcast_service requires a site_config", aborting the
                        # stage and starving the video director (0 shot lists).
                        # Script-safe variant (2026-08-01): structural cleanup
                        # only — pronunciations now live at the TTS boundary, so
                        # phonetic spellings are no longer frozen into scripts.
                        lambda t: _normalize_for_script(t, site_config=sc),
                    )
                    if attempt_scenes:
                        video_scenes = attempt_scenes
                    if attempt_short and len(attempt_short.split()) >= short_min_words:
                        short_summary = attempt_short
                        break
                    logger.info(
                        "[MEDIA] short script invalid (%d words < %d min) on "
                        "attempt %d%s",
                        len((attempt_short or "").split()), short_min_words,
                        scene_attempt + 1,
                        " — retrying once" if scene_attempt == 0 else "",
                    )
                if video_scenes or short_summary:
                    logger.info(
                        "[MEDIA] Video scenes: %d, Short summary: %d chars",
                        len(video_scenes), len(short_summary),
                    )
                if scene_output and not short_summary:
                    # Both attempts produced no usable short. No short beats a
                    # frozen-garbage short: the render lane treats a missing
                    # short_shot_list as a graceful no-op, and a later regen
                    # can supply one — a frozen **END** cannot self-heal.
                    emit_finding(
                        source="media.generate_scripts",
                        kind="short_script_invalid",
                        title="short script unusable after retry — task ships without a short",
                        body=(
                            f"Task {context.get('task_id')}: both scene-call "
                            f"attempts produced a short below "
                            f"video_short_min_words ({short_min_words}) after "
                            "meta-commentary sanitization. The task proceeds "
                            "with no short-form script (long lane unaffected); "
                            "regenerate media scripts to add one."
                        ),
                        severity="warn",
                        dedup_key=f"short_script_invalid:{context.get('task_id')}",
                        extra={
                            "task_id": str(context.get("task_id") or ""),
                            "min_words": short_min_words,
                        },
                    )

            if short_summary:
                # Runaway-short cap (#867): a model that ignores the target can
                # emit a 2-3 minute "short". Trim to the last full sentence
                # within video_short_max_seconds so the narration can't outrun a
                # short-form timeline (the renderer's narration-fit stretches the
                # remaining gap). Deterministic, no LLM call, sentence-safe.
                if True:
                    short_max_s = sc.get_int("video_short_max_seconds", 60) if sc is not None else 60
                    max_short_words = round(short_max_s * _WORDS_PER_SECOND)
                    short_summary, orig_w, kept_w = _trim_to_word_budget(
                        short_summary, max_short_words,
                    )
                    if kept_w < orig_w:
                        logger.info(
                            "[MEDIA] short script trimmed %d->%d words (max %d)",
                            orig_w, kept_w, max_short_words,
                        )
                        emit_finding(
                            source="media.generate_scripts",
                            kind="short_script_trimmed",
                            title=f"short script trimmed {orig_w}->{kept_w} words",
                            body=(
                                f"The short summary for task {context.get('task_id')} ran "
                                f"{orig_w} words (~{orig_w / _WORDS_PER_SECOND:.0f}s), over the "
                                f"video_short_max_seconds budget of {short_max_s}s "
                                f"({max_short_words} words). Trimmed to the last full sentence "
                                f"within budget ({kept_w} words) so the short stays short. "
                                "Advisory — tighten the short prompt if this is frequent."
                            ),
                            severity="info",
                            dedup_key=f"short_script_trimmed:{context.get('task_id')}",
                            extra={
                                "task_id": str(context.get("task_id") or ""),
                                "original_words": orig_w,
                                "trimmed_words": kept_w,
                                "max_words": max_short_words,
                            },
                        )

            # Audio gen — ambient video bed via StableAudioOpen. Gated off in
            # video-only mode: unlike the podcast blocks (dead once Call 1 is
            # skipped), this keys off video_scenes, which video-only DOES
            # produce — without the gate a regen would synthesize a fresh bed
            # as a side effect.
            ambient_audio_path = ""
            if video_scenes and not video_only and is_audio_gen_enabled(sc):
                try:
                    # Music-directed template (operator-tunable); the scene
                    # text rides along as mood cues only — verbatim visual
                    # prompts make the model render room tone, not music.
                    mood = _ambient_mood_cues(video_scenes) or (title or "calm focus")
                    template = sc.get(
                        "audio_gen_ambient_prompt_template", _AMBIENT_PROMPT_FALLBACK,
                    ) or _AMBIENT_PROMPT_FALLBACK
                    ambient_prompt = template.replace("{mood}", mood)
                    ambient_result = await generate_audio(
                        ambient_prompt,
                        "ambient",
                        site_config=sc,
                        # Model max (~47s): the compositor loops the bed under
                        # the whole video, so the longest clip = fewest audible
                        # loop seams (provider default 5s is sting-sized).
                        duration_s=47.0,
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
        except GpuBusyError as busy:
            # Ahead of the broad handler on purpose (poindexter#914 P2 group 2):
            # a contention skip is the admission contract working, not script
            # generation failing, and folding it into the generic warning would
            # make render pressure read as this stage breaking. Degrades exactly
            # like that handler — same partial-work preservation, so a podcast
            # script built before the skip still reaches the video director.
            logger.info(
                "[MEDIA] Script generation skipped — GPU busy (%s)", busy.reason,
            )
            surface_media_gpu_busy_skip(
                "media_scripts", busy, task_id=context.get("task_id"),
            )
            stages = context.setdefault("stages", {})
            stages["4b_media_scripts"] = False
            return StageResult(
                ok=bool(podcast_script),
                detail=(
                    f"gpu_busy_skip: {busy.reason} "
                    f"(podcast_script={len(podcast_script)}c preserved)"
                ),
                context_updates={
                    "podcast_script": podcast_script,
                    "podcast_script_length": len(podcast_script),
                    "podcast_audio_path": podcast_audio_path,
                    "podcast_intro_audio_path": podcast_intro_audio_path,
                    "video_long_script": video_long_script,
                    "stages": stages,
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


def _first_headline(content: str) -> str:
    """Return the first Markdown H1/H2 heading text, or '' if none.

    H2 counts because the writer emits the article headline as ``## Title`` —
    an H1-only match silently found nothing and fell through to ``seo_title``,
    so the podcast announced a *different* headline than the published post
    ("Debt in AI Infrastructure: The $1.65 Trillion Secret" spoken over an
    article titled "The invoice nobody wants to show you"). The H1-only form
    degraded as the writer drifted to ``##``: 2.5% of episodes hit the fallback
    in April 2026, 62% by July. Measured over the 76 podcast episodes since
    2026-06-01, the first H1-or-H2 equals the published ``posts.title`` in 73
    (96%); the 3 misses are articles that genuinely open on a section heading.
    """
    for line in (content or "").splitlines():
        match = re.match(r"#{1,2}\s+(\S.*)", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _resolve_media_title(context: dict[str, Any]) -> str:
    """Resolve the reader-facing title for media narration (Bug C2).

    Media narration — the podcast intro ("Today's episode: …") and the video
    voiceover — is spoken aloud, so it must speak the FULL, clean title.

    Two channels are unfit as the first choice:

    - ``title`` can carry a polluted value: a style-rubric line leaked by
      content.generate_title lands in ``pipeline_versions.title`` even when the
      published post recovers a clean title from its content headline.
    - ``seo_title`` is clamped to <=60 chars at a word boundary for the search
      snippet, so on a long title it truncates mid-phrase — the "… Testing"
      podcast-intro bug, where the full title was "… Testing Its Quality". A
      spoken intro has no character budget, so the clipped form is wrong here.
      It is also frequently a *different* headline than the one published, so
      reaching it at all means the intro announces the wrong episode.

    Prefer the full clean title from the content headline (the H1/H2 the reader
    sees), then the clean-but-possibly-clipped ``seo_title``, then the raw
    ``title`` channel only as a last resort.
    """
    headline = _first_headline(context.get("content") or "")
    if headline:
        return headline
    seo_title = (context.get("seo_title") or "").strip()
    if seo_title:
        return seo_title
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
    "- Aim for a ~{target_seconds}-second narration (about {target_words} "
    "words of spoken prose).\n"
    "- COLD OPEN: start mid-thought on the article's strongest concrete fact "
    "or tension. Never open with a greeting or a scene-setting frame — no "
    "\"Welcome\", \"In today's\", \"Let's explore\", \"Imagine\", "
    "\"deep dive\".\n"
    "- Close on the article's final insight in one natural sentence. Never "
    "\"In conclusion\", \"In summary\", \"To wrap up\". Do NOT add a "
    "like/subscribe call-to-action — that is appended separately.\n"
    "- Keep every number, dollar figure, and statistic exactly as the "
    "article states it — the numbers are the substance.\n"
    "- Banned words and phrases: delve, tapestry, testament, game-changer, "
    "revolutionize.\n"
    "- Plain spoken prose. Commas and periods, not semicolons. No headings, "
    "no stage directions, no emojis, no markdown.\n\n"
    "TITLE: {title}\n\n"
    "ARTICLE:\n{content}\n\n"
    "NARRATION:"
)


def _build_video_narration_prompt(
    title: str,
    clean_content: str,
    *,
    target_seconds: int,
    target_words: int,
) -> str:
    """Prompt for the long-form VIDEO narration script.

    Pure spoken narration (Bug A) — the renderer shows generic static imagery,
    so the script never references on-screen visuals. Operator-tunable via
    UnifiedPromptManager (``video.long_form_narration``); the module-level
    fallback mirrors the SKILL.md default for tests / bootstrap.

    The second/word target is substituted from ``video_long_target_seconds``
    (silent-tail fix, 2026-07-31) — never hardcoded — the same one-canonical-
    target pattern as the short lane (#867). The prompt used to carry NO length
    ask at all, so narration length was whatever the model felt like (358-592
    words observed) while the director planned visuals from the much longer
    podcast script — every long video ran minutes past the voice.
    """
    content = clean_content[:3500]
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            "video.long_form_narration",
            title=title,
            content=content,
            target_seconds=target_seconds,
            target_words=target_words,
        )
    except Exception:  # noqa: BLE001 — prompt resolution is best-effort
        return _VIDEO_NARRATION_FALLBACK.format(
            title=title,
            content=content,
            target_seconds=target_seconds,
            target_words=target_words,
        )


def _build_scene_prompt(
    title: str,
    clean_content: str,
    site_name: str,
    *,
    target_seconds: int,
    target_words: int,
) -> str:
    """Build the prompt for the video-scenes + short-summary LLM call.

    The short narration's second/word target is substituted from
    ``video_short_target_seconds`` (issue #867) — never hardcoded — so the prompt
    ask and the shot-list clamp agree (the old 60s/150w prompt vs a 45s clamp
    guaranteed a ~15s frozen tail on every compliant script).
    """
    return (
        "Generate TWO things for a blog post video:\n\n"
        "PART 1 — Write 6-8 numbered lines, each describing a photorealistic image "
        "for a video slideshow about this article. Each line is a Stable Diffusion XL prompt. "
        "Requirements: cinematic lighting, no people, no text, no faces, no hands, 4K quality. "
        "One scene per line.\n\n"
        "PART 2 — After a blank line, write \"SHORT:\" on its own line, then write a "
        f"~{target_seconds}-second narration (about {target_words} words) "
        "summarizing the article for TikTok/YouTube Shorts. "
        f"Start with a hook, cover 2-3 key takeaways, end with \"Full article at {site_name}.\"\n"
        "Narration rules: spoken prose only — no emojis, no markdown, no "
        "hashtags, at most one exclamation mark. Open on the article's single "
        "most surprising concrete fact — never with \"Ever wondered\", "
        "\"Imagine\", or a question cliche. Keep every number and statistic "
        "exactly as the article states it. Use commas and periods, not "
        "semicolons. Output NOTHING after the narration text — no notes, no "
        "commentary about the script, no END marker.\n\n"
        f"ARTICLE: {title}\n\n"
        f"{clean_content[:3000]}\n\n"
        "SCENES:"
    )


# Sentence boundary for the runaway-short trim (#867): split after . ! ? + space.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _trim_to_word_budget(text: str, max_words: int) -> tuple[str, int, int]:
    """Trim ``text`` to the last COMPLETE sentence within ``max_words``.

    Returns ``(trimmed, original_words, kept_words)``. No-op when already within
    budget. If the very first sentence already exceeds the budget (a single
    run-on), hard-cut at ``max_words`` as a last resort (rare). Never cuts
    mid-sentence otherwise — the short narration stays coherent (the same
    concern #1874's mid-sentence-cut fix protected).
    """
    words = text.split()
    original = len(words)
    if original <= max_words:
        return text, original, original
    kept: list[str] = []
    count = 0
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        w = len(sentence.split())
        if count + w > max_words:
            break
        kept.append(sentence)
        count += w
    trimmed = " ".join(kept).strip()
    if not trimmed:
        return " ".join(words[:max_words]).strip(), original, max_words
    return trimmed, original, count


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


# Meta-commentary a model appends AFTER the narration it was asked for —
# stage notes about its own output, sign-offs, or bare end markers. These are
# not narration and must never reach TTS: a frozen short once read "These
# elements aim to visually and narratively encapsulate the essence…" aloud
# into the render, and another consisted entirely of "**END**".
_SHORT_META_LINE = re.compile(
    r"^\s*(?:"
    r"these\s+(?:elements|scenes|visuals)\b"
    r"|this\s+(?:script|narration|video|short)\b"
    r"|note[:\s]"
    r"|\**\s*(?:end|fin|stop)\s*\**\s*$"
    r"|\(?\s*end\s+of\s+(?:script|narration|short)\s*\)?"
    r")",
    re.IGNORECASE,
)
# A markdown horizontal rule — everything after it is the model talking ABOUT
# the script, not the script.
_HR_LINE = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")


def _sanitize_short_script(short_raw: str) -> str:
    """Cut model meta-commentary off a raw short script (2026-08-01).

    1. Truncate at the first horizontal-rule line.
    2. Drop trailing paragraphs whose first line is meta-commentary (see
       ``_SHORT_META_LINE``) — from the end only, so a legit narration
       paragraph that merely mentions "this" mid-script survives.
    """
    lines = short_raw.split("\n")
    kept: list[str] = []
    for ln in lines:
        if _HR_LINE.match(ln):
            break
        kept.append(ln)
    paras = _PARA_BREAK.split("\n".join(kept).strip())
    while paras:
        first_line = next(
            (ln for ln in paras[-1].split("\n") if ln.strip()), "",
        )
        if first_line and _SHORT_META_LINE.match(first_line):
            paras.pop()
            continue
        break
    return "\n\n".join(p for p in paras if p.strip()).strip()


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
    short_raw = _sanitize_short_script(short_raw) if short_raw else ""
    short_summary = normalize_for_speech(short_raw) if short_raw else ""
    return _extract_scene_lines(scenes_raw), short_summary
