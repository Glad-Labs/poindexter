"""StitchShortFormStage — render the short-form vertical video.

Step 10 of the video pipeline (Glad-Labs/poindexter#143). Per Matt's
call ("long-form and short-form should each be a stage most likely,
whatever provides the most flexibility"), short-form is its own
Stage so each side can be tuned independently. This Stage handles
the 1080×1920 vertical short-form deliverable for YouTube Shorts /
TikTok / Reels.

## Context reads

- ``video_script`` (dict)         — from ScriptForVideoStage
- ``video_scene_visuals`` (dict)  — from SceneVisualsStage
- ``video_tts`` (dict)            — from TtsForVideoStage
- ``post_id`` (UUID), ``task_id`` (str), ``site_config``

## Context writes

- ``video_outputs.short_form`` (dict) — see ``StageResult.context_updates`` below
- ``stages["video.stitch_short_form"]`` (bool)

The ``video_outputs`` shape mirrors long-form::

    {
        "short_form": {
            "output_path": "/abs/local.mp4",
            "public_url": "https://...",       # empty when upload failed
            "media_asset_id": "uuid-str",      # None when DB write failed
            "duration_s": float,
            "width": int, "height": int, "fps": int,
            "file_size_bytes": int,
            "srt_path": "/abs/sidecar.srt",
        }
    }

Logic + asset persistence + SRT derivation live in
:mod:`services.stages._video_stitch` and are reused unchanged from
the long-form sibling Stage.

## Differences from long_form

- Reads ``video_script.short_form`` / ``video_scene_visuals.short_form`` /
  ``video_tts.short_form`` (not ``long_form``).
- 1080×1920 vertical instead of 1920×1080 landscape.
- No ``outro_cta`` — short-form ends on its last hook scene per the
  ScriptForVideoStage contract, so the SRT has no outro block.
- Hard 60-second total cap: if the sum of scene durations exceeds 60s,
  trailing scenes are dropped from the end (with a warning logged) so
  the output complies with platform constraints. Never raises.
- ``output_filename = "<task_id>_short.mp4"``;
  ``asset_type = "video_short"``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.media_compositor import CompositionRequest
from plugins.stage import StageResult

from services.stages._video_stitch import (
    StitchSpec,
    build_scenes,
    derive_srt,
    output_paths,
    persist_media_asset,
    resolve_compositor,
    upload_to_object_storage,
    write_srt_sidecar,
)

logger = logging.getLogger(__name__)


_SHORT_FORM_SPEC = StitchSpec(
    format_kind="short_form",
    asset_type="video_short",
    width=1080,
    height=1920,
    fps=30,
    include_outro=False,
    output_filename="{task_id}_short.mp4",
)

# Matches ``_SHORT_FORM_SCENE_SECONDS`` in ``script_for_video.py`` so
# scenes that come through without a TTS-derived duration land at the
# same fallback the script Stage hinted to the LLM.
_SHORT_FORM_FALLBACK_SCENE_S = 13

# Hard platform cap. Shorts / TikTok / Reels all reject >60s as the
# short-form rail; the upload Stage will refuse the file if we go
# over, so trim here instead of failing late.
_SHORT_FORM_MAX_DURATION_S = 60.0


def _enforce_duration_cap(scenes: list[Any]) -> list[Any]:
    """Trim trailing scenes until total duration ≤ 60 seconds.

    Drops scenes off the *end* of the list (preserves the hook + early
    payload — losing the last scene of a Short is far better than
    truncating the opener) and logs a warning so the operator sees
    what got cut. Returns the (possibly shortened) list. Never
    raises — short-form is best-effort.
    """
    total = sum(float(getattr(sc, "duration_s", 0.0) or 0.0) for sc in scenes)
    if total <= _SHORT_FORM_MAX_DURATION_S:
        return scenes

    trimmed = list(scenes)
    dropped = 0
    while trimmed and total > _SHORT_FORM_MAX_DURATION_S:
        last = trimmed.pop()
        total -= float(getattr(last, "duration_s", 0.0) or 0.0)
        dropped += 1

    logger.warning(
        "[video.stitch_short_form] total duration exceeded %.1fs cap; "
        "dropped %d trailing scene(s), final duration ~%.1fs",
        _SHORT_FORM_MAX_DURATION_S,
        dropped,
        total,
    )
    return trimmed


class StitchShortFormStage:
    """Render the 1080×1920 short-form vertical video for the post."""

    name = "video.stitch_short_form"
    description = (
        "Stitch short-form (1080×1920) vertical video for Shorts / TikTok / Reels"
    )
    timeout_seconds = 600  # short-form encodes are quick — vertical, ≤60s
    halts_on_failure = False  # pipeline continues even when short-form fails

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],  # pyright: ignore[reportUnusedParameter]
    ) -> StageResult:
        site_config = context.get("site_config")
        if site_config is None:
            return StageResult(
                ok=False,
                detail="site_config missing on context",
                metrics={"skipped": True},
            )

        script = (context.get("video_script") or {}).get("short_form") or {}
        visuals = (context.get("video_scene_visuals") or {}).get("short_form") or []
        tts = (context.get("video_tts") or {}).get("short_form") or {}
        if not script.get("scenes") or not tts.get("scenes"):
            return StageResult(
                ok=False,
                detail="short-form upstream missing scenes (script or tts)",
                metrics={"skipped": True},
            )

        compositor = resolve_compositor(site_config)
        if compositor is None:
            return StageResult(
                ok=False,
                detail="no MediaCompositor registered (tried video_compositor + ffmpeg_local fallback)",
            )

        task_id = str(context.get("task_id") or "untagged")
        post_id = context.get("post_id")
        output_dir, output_path = output_paths(_SHORT_FORM_SPEC, task_id)

        full_visuals = context.get("video_scene_visuals") or {}
        scenes = build_scenes(
            visuals=visuals,
            tts_scenes=tts.get("scenes") or [],
            fallback_duration_s=_SHORT_FORM_FALLBACK_SCENE_S,
            intro_audio_path=str(tts.get("intro_audio_path") or ""),
            intro_duration_s=float(tts.get("intro_duration_s") or 0.0),
            intro_clip_path=str(full_visuals.get("intro_clip_path") or ""),
            # Short-form has no outro per the StitchSpec — leaving the
            # outro_* params at their defaults skips the bookend.
        )
        if not scenes:
            return StageResult(
                ok=False,
                detail="no usable scenes after filtering missing visuals/audio",
            )

        # Enforce the 60s platform cap *before* SRT derivation so the
        # caption track and composition both reflect the trimmed set.
        scenes = _enforce_duration_cap(scenes)
        if not scenes:
            return StageResult(
                ok=False,
                detail="no scenes remaining after applying 60s short-form cap",
            )

        # Build a parallel TTS-pair list filtered to the same scene set
        # we kept after trimming, so derive_srt sees only the scenes
        # that will actually appear in the video.
        kept_clip_paths = {sc.clip_path for sc in scenes}
        tts_scene_pairs: list[tuple[str, float]] = []
        # Re-zip from the TTS payload by scene_idx, in the same sorted
        # order build_scenes uses, then drop any whose clip_path was
        # filtered out.
        visuals_by_idx = {
            int(v["scene_idx"]): v
            for v in visuals
            if isinstance(v.get("scene_idx"), int)
        }
        tts_by_idx = {
            int(s["scene_idx"]): s
            for s in (tts.get("scenes") or [])
            if isinstance(s.get("scene_idx"), int)
        }
        for idx in sorted(set(visuals_by_idx) | set(tts_by_idx)):
            v = visuals_by_idx.get(idx) or {}
            t = tts_by_idx.get(idx) or {}
            clip_path = str(v.get("clip_path") or "")
            if not clip_path or clip_path not in kept_clip_paths:
                continue
            duration_s = float(t.get("duration_s") or 0.0)
            if duration_s <= 0:
                duration_s = float(_SHORT_FORM_FALLBACK_SCENE_S)
            tts_scene_pairs.append((str(t.get("text") or ""), duration_s))

        # Short-form has no outro_cta per ScriptForVideoStage's output
        # shape — pass empty/zero so derive_srt skips the outro block.
        srt_text = derive_srt(
            intro_text=str(script.get("intro_hook") or ""),
            intro_duration_s=float(tts.get("intro_duration_s") or 0.0),
            scene_pairs=tts_scene_pairs,
            outro_text="",
            outro_duration_s=0.0,
        )
        srt_path = write_srt_sidecar(
            srt_text, output_dir, stem=f"{task_id}_short",
        )

        request = CompositionRequest(
            scenes=scenes,
            soundtrack_path=None,  # soundtrack is a future toggle
            caption_track_path=srt_path or None,
            output_path=output_path,
            width=_SHORT_FORM_SPEC.width,
            height=_SHORT_FORM_SPEC.height,
            fps=_SHORT_FORM_SPEC.fps,
            codec="h264",
            container="mp4",
            metadata={
                "post_id": str(post_id or ""),
                "task_id": task_id,
                "format": "short_form",
            },
        )

        result = await compositor.compose(
            request,
            _site_config=site_config,
            task_id=task_id,
            phase="stitch_short_form",
        )
        if not result.success or not result.output_path:
            return StageResult(
                ok=False,
                detail=f"compositor failed: {result.error or 'no error string'}",
                metrics={
                    "compositor": getattr(compositor, "name", "unknown"),
                    "duration_ms": result.metadata.get("duration_ms"),
                },
            )

        # Upload + persist (best-effort).
        public_url = await upload_to_object_storage(
            local_path=result.output_path,
            asset_type=_SHORT_FORM_SPEC.asset_type,
            post_id=post_id,
            site_config=site_config,
        )
        media_asset_id = await persist_media_asset(
            pool=getattr(site_config, "_pool", None),
            post_id=post_id,
            asset_type=_SHORT_FORM_SPEC.asset_type,
            provider_plugin=f"compositor.{getattr(compositor, 'name', 'unknown')}",
            output_path=result.output_path,
            public_url=public_url,
            width=result.width or _SHORT_FORM_SPEC.width,
            height=result.height or _SHORT_FORM_SPEC.height,
            duration_ms=int(result.duration_s * 1000) if result.duration_s else 0,
            file_size_bytes=result.file_size_bytes,
            cost_usd=result.cost_usd,
            electricity_kwh=result.electricity_kwh,
            metadata={
                "compositor": getattr(compositor, "name", "unknown"),
                "task_id": task_id,
                "scene_count": len(scenes),
                "fps": result.fps,
                "codec": result.codec,
                "srt_path": srt_path,
                **(result.metadata or {}),
            },
        )

        stages = context.setdefault("stages", {})
        stages[self.name] = True

        outputs = context.setdefault("video_outputs", {})
        outputs["short_form"] = {
            "output_path": result.output_path,
            "public_url": public_url,
            "media_asset_id": media_asset_id,
            "duration_s": result.duration_s,
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "file_size_bytes": result.file_size_bytes,
            "srt_path": srt_path,
        }

        return StageResult(
            ok=True,
            detail=(
                f"short-form rendered: {result.duration_s:.1f}s, "
                f"{result.width}x{result.height}, "
                f"{result.file_size_bytes // 1024} KiB"
            ),
            context_updates={
                "video_outputs": outputs,
                "stages": stages,
            },
            metrics={
                "duration_s": result.duration_s,
                "file_size_bytes": result.file_size_bytes,
                "cost_usd": result.cost_usd,
                "electricity_kwh": result.electricity_kwh,
                "uploaded": bool(public_url),
                "media_asset_id": media_asset_id,
            },
        )
