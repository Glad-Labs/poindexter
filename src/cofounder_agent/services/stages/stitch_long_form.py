"""StitchLongFormStage — render the long-form video.

Step 9 of the video pipeline (Glad-Labs/poindexter#143). Per Matt's
call: long-form and short-form are *separate* Stages so each side
can be tuned independently. This Stage handles the 1920×1080
landscape long-form deliverable.

## Context reads

- ``video_script`` (dict)         — from ScriptForVideoStage
- ``video_scene_visuals`` (dict)  — from SceneVisualsStage
- ``video_tts`` (dict)            — from TtsForVideoStage
- ``post_id`` (UUID), ``task_id`` (str), ``site_config``

## Context writes

- ``video_outputs.long_form`` (dict) — see ``StageResult.context_updates`` below
- ``stages["video.stitch_long_form"]`` (bool)

The ``video_outputs`` shape::

    {
        "long_form": {
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
:mod:`services.stages._video_stitch` so the short-form sibling Stage
stays a near-copy with format-specific spec swapped in.
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


_LONG_FORM_SPEC = StitchSpec(
    format_kind="long_form",
    asset_type="video_long",
    width=1920,
    height=1080,
    fps=30,
    include_outro=True,
    output_filename="{task_id}_long.mp4",
)

_LONG_FORM_FALLBACK_SCENE_S = 30


class StitchLongFormStage:
    """Render the 1920×1080 long-form video for the post."""

    name = "video.stitch_long_form"
    description = "Stitch long-form (1920×1080) video from scenes + narration"
    timeout_seconds = 1800  # 16-scene long-form on slow disk
    halts_on_failure = False  # short-form still attempts on failure

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

        script = (context.get("video_script") or {}).get("long_form") or {}
        visuals = (context.get("video_scene_visuals") or {}).get("long_form") or []
        tts = (context.get("video_tts") or {}).get("long_form") or {}
        if not script.get("scenes") or not tts.get("scenes"):
            return StageResult(
                ok=False,
                detail="long-form upstream missing scenes (script or tts)",
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
        output_dir, output_path = output_paths(_LONG_FORM_SPEC, task_id)

        scenes = build_scenes(
            visuals=visuals,
            tts_scenes=tts.get("scenes") or [],
            fallback_duration_s=_LONG_FORM_FALLBACK_SCENE_S,
        )
        if not scenes:
            return StageResult(
                ok=False,
                detail="no usable scenes after filtering missing visuals/audio",
            )

        # Derive an SRT sidecar from the script + TTS timing. Cheap,
        # deterministic, and good enough for V0. Whisper.cpp-derived
        # captions are an opt-in upgrade for accents the script
        # smoothes over.
        srt_text = derive_srt(
            intro_text=str(script.get("intro_hook") or ""),
            intro_duration_s=float(tts.get("intro_duration_s") or 0.0),
            scene_pairs=[
                (str(sc.get("text") or ""), float(sc.get("duration_s") or 0.0))
                for sc in (tts.get("scenes") or [])
            ],
            outro_text=str(script.get("outro_cta") or ""),
            outro_duration_s=float(tts.get("outro_duration_s") or 0.0),
        )
        srt_path = write_srt_sidecar(
            srt_text, output_dir, stem=f"{task_id}_long",
        )

        request = CompositionRequest(
            scenes=scenes,
            soundtrack_path=None,  # soundtrack is a future toggle
            caption_track_path=srt_path or None,
            output_path=output_path,
            width=_LONG_FORM_SPEC.width,
            height=_LONG_FORM_SPEC.height,
            fps=_LONG_FORM_SPEC.fps,
            codec="h264",
            container="mp4",
            metadata={
                "post_id": str(post_id or ""),
                "task_id": task_id,
                "format": "long_form",
            },
        )

        result = await compositor.compose(
            request,
            _site_config=site_config,
            task_id=task_id,
            phase="stitch_long_form",
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
            asset_type=_LONG_FORM_SPEC.asset_type,
            post_id=post_id,
            site_config=site_config,
        )
        media_asset_id = await persist_media_asset(
            pool=getattr(site_config, "_pool", None),
            post_id=post_id,
            asset_type=_LONG_FORM_SPEC.asset_type,
            provider_plugin=f"compositor.{getattr(compositor, 'name', 'unknown')}",
            output_path=result.output_path,
            public_url=public_url,
            width=result.width or _LONG_FORM_SPEC.width,
            height=result.height or _LONG_FORM_SPEC.height,
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
        outputs["long_form"] = {
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
                f"long-form rendered: {result.duration_s:.1f}s, "
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
