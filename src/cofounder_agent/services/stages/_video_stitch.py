"""Shared helpers for the long-form / short-form stitch Stages.

The two ``stitch_*`` Stages share ~80% of their logic — both gather
upstream context, build a ``CompositionRequest``, drive the
configured ``MediaCompositor``, and persist a ``media_assets`` row.
The thing that *does* differ — output dimensions, scene selection,
caption derivation, asset type label — fits cleanly behind a small
``StitchSpec`` dataclass.

Kept private (underscore prefix) and not registered as a Stage. This
module exists so ``stitch_long_form.py`` and ``stitch_short_form.py``
stay thin enough that comparing them visually shows the format
differences and nothing else.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StitchSpec:
    """The pieces that vary between long-form and short-form stitching."""

    format_kind: str  # "long_form" or "short_form"
    asset_type: str  # "video_long" / "video_short" — written to media_assets.type
    width: int
    height: int
    fps: int
    include_outro: bool  # long-form has an outro; short-form doesn't
    output_filename: str  # "<task_id>_long.mp4" / "<task_id>_short.mp4"


# ---------------------------------------------------------------------------
# Compositor resolution
# ---------------------------------------------------------------------------


def resolve_compositor(site_config: Any) -> Any | None:
    """Pick the registered MediaCompositor named by ``video_compositor``.

    Defaults to ``ffmpeg_local`` so the V0 pipeline works on a fresh
    install without any settings tuning. Returns ``None`` when
    discovery fails — caller surfaces ``success=False``.
    """
    name = "ffmpeg_local"
    if site_config is not None:
        configured = site_config.get("video_compositor", "")
        if configured:
            name = str(configured)

    try:
        from plugins.registry import _cached, ENTRY_POINT_GROUPS
    except Exception as exc:
        logger.warning("[video.stitch] registry import failed: %s", exc)
        return None

    try:
        instances = _cached(ENTRY_POINT_GROUPS["media_compositors"])
    except KeyError:
        # Brand-new entry-point group — registry may not have a getter
        # exposed yet but _cached works against any group string.
        instances = ()
    except Exception as exc:
        logger.warning("[video.stitch] compositor discovery failed: %s", exc)
        return None

    for instance in instances:
        if getattr(instance, "name", None) == name:
            return instance

    # Fallback: try the V0 default directly. Avoids a no-compositor
    # failure when entry-points haven't been re-installed since the
    # plugin was added.
    if name != "ffmpeg_local":
        for instance in instances:
            if getattr(instance, "name", None) == "ffmpeg_local":
                return instance

    # Last-ditch: instantiate the canonical compositor by import. Means
    # operators don't have to ``poetry install`` to get a working
    # pipeline after a fresh git pull — a real concern with how the
    # entry-points cache lifecycle works on Windows.
    try:
        from services.media_compositors.ffmpeg_local import (
            FFmpegLocalCompositor,
        )
        return FFmpegLocalCompositor(site_config=site_config)
    except Exception as exc:
        logger.warning("[video.stitch] ffmpeg_local import failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Scene assembly
# ---------------------------------------------------------------------------


def build_scenes(
    *,
    visuals: list[dict[str, Any]],
    tts_scenes: list[dict[str, Any]],
    fallback_duration_s: int,
    intro_audio_path: str = "",
    intro_duration_s: float = 0.0,
    outro_audio_path: str = "",
    outro_duration_s: float = 0.0,
    intro_clip_path: str = "",
    outro_clip_path: str = "",
) -> list[Any]:
    """Zip per-scene visuals + audio into ``CompositionScene`` objects.

    Returns a list of ``plugins.media_compositor.CompositionScene``.
    Skips scenes missing either a clip_path or audio_path —
    short-form misses are graceful-degrade per the upstream Stages'
    contracts.

    Bookends — when ``intro_audio_path`` / ``outro_audio_path`` are
    supplied, prepend/append a CompositionScene that reuses the first
    or last body scene's visual but plays the intro/outro narration
    over it. This keeps the SRT track (built by :func:`derive_srt`)
    aligned with the rendered video. Without bookend scenes, the
    intro/outro audio that the upstream tts_for_video Stage produces
    gets dropped silently and the SRT timing offsets the captions
    against the actual video — Matt caught the symptom on the V0
    sample run.
    """
    from plugins.media_compositor import CompositionScene

    visuals_by_idx = {
        int(v["scene_idx"]): v
        for v in visuals
        if isinstance(v.get("scene_idx"), int)
    }
    tts_by_idx = {
        int(s["scene_idx"]): s
        for s in tts_scenes
        if isinstance(s.get("scene_idx"), int)
    }

    body_scenes: list[CompositionScene] = []
    body_visuals: list[str] = []  # parallel list — visual for each body scene, in order
    for idx in sorted(set(visuals_by_idx) | set(tts_by_idx)):
        v = visuals_by_idx.get(idx) or {}
        t = tts_by_idx.get(idx) or {}

        clip_path = str(v.get("clip_path") or "")
        narration_path = str(t.get("audio_path") or "") or None
        if not clip_path:
            continue  # no visual = drop the scene
        # If TTS produced timing, use it; otherwise fall back to the
        # script's hint. duration=0 means "use clip native" but that's
        # never what we want for a still-image scene with narration.
        duration_s = float(t.get("duration_s") or 0.0)
        if duration_s <= 0:
            duration_s = float(fallback_duration_s)

        body_scenes.append(
            CompositionScene(
                clip_path=clip_path,
                narration_path=narration_path,
                duration_s=duration_s,
                caption_text="",  # full SRT track handled separately
            ),
        )
        body_visuals.append(clip_path)

    if not body_scenes:
        return []

    # Bookend visual selection. Prefer dedicated intro/outro images
    # supplied by the SceneVisualsStage (fetched via separate Pexels
    # queries keyed off intro_hook / outro_cta) so the bookend is
    # topically related to the hook but never duplicates a body
    # scene's image. Falls back to non-adjacent body visuals when
    # the dedicated lookup didn't return anything — better than
    # nothing and at least separates the dupe by a full body scene.
    intro_visual = (
        intro_clip_path
        or (body_visuals[1] if len(body_visuals) > 1 else body_visuals[0])
    )
    outro_visual = (
        outro_clip_path
        or (body_visuals[-2] if len(body_visuals) > 1 else body_visuals[-1])
    )

    scenes: list[CompositionScene] = []
    if intro_audio_path and intro_duration_s > 0:
        scenes.append(
            CompositionScene(
                clip_path=intro_visual,
                narration_path=intro_audio_path,
                duration_s=intro_duration_s,
                caption_text="",
            ),
        )
    scenes.extend(body_scenes)
    if outro_audio_path and outro_duration_s > 0:
        scenes.append(
            CompositionScene(
                clip_path=outro_visual,
                narration_path=outro_audio_path,
                duration_s=outro_duration_s,
                caption_text="",
            ),
        )
    return scenes


# ---------------------------------------------------------------------------
# SRT derivation from script + TTS timing
# ---------------------------------------------------------------------------


def _format_srt_timestamp(seconds: float) -> str:
    """Format ``seconds`` as ``HH:MM:SS,mmm`` per SRT spec."""
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis -= 1000
        whole_seconds += 1
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def derive_srt(
    *,
    intro_text: str,
    intro_duration_s: float,
    scene_pairs: list[tuple[str, float]],
    outro_text: str = "",
    outro_duration_s: float = 0.0,
) -> str:
    """Build an SRT document from ordered (text, duration) pairs.

    Cheaper and more deterministic than running whisper.cpp on the
    rendered audio; the script we synthesized is canonically what the
    narrator said. ``WhisperLocalCaptionProvider`` is available for
    callers that need acoustic-derived timing instead.
    """
    blocks: list[str] = []
    cursor = 0.0
    seq = 1

    def emit(text: str, dur: float) -> None:
        nonlocal cursor, seq
        cleaned = (text or "").strip()
        if not cleaned or dur <= 0:
            return
        start = _format_srt_timestamp(cursor)
        end = _format_srt_timestamp(cursor + dur)
        blocks.append(f"{seq}\n{start} --> {end}\n{cleaned}\n")
        seq += 1
        cursor += dur

    if intro_text:
        emit(intro_text, intro_duration_s)
    for text, dur in scene_pairs:
        emit(text, dur)
    if outro_text:
        emit(outro_text, outro_duration_s)

    return "\n".join(blocks)


def write_srt_sidecar(srt_text: str, output_dir: Path, stem: str) -> str:
    """Persist ``srt_text`` next to where the video lands.

    Returns the absolute path. Empty string when there's nothing to
    write — caller treats that as "no caption track."
    """
    if not srt_text.strip():
        return ""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}.srt"
    path.write_text(srt_text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------


def _video_output_root() -> Path:
    """Resolve the long-lived video output directory.

    Mirrors podcast_service / video_service: ``~/.poindexter/video``
    on the worker's filesystem (or the bind-mount root when running
    in Docker). Returned as a real directory the caller can write to.
    """
    override = os.environ.get("POINDEXTER_DATA_ROOT")
    if override:
        root = Path(override) / "video"
    else:
        docker_root = Path("/root/.poindexter")
        # ``is_dir()`` raises PermissionError on filesystems where
        # ``/root`` exists but is not stat-able by the current user
        # (GitHub Actions runners — `/root` is owned by root, mode 700).
        # Treat any OSError as "not the docker root" and fall through
        # to the home-dir branch.
        try:
            is_docker_root = docker_root.is_dir()
        except OSError:
            is_docker_root = False
        if is_docker_root:
            root = docker_root / "video"
        else:
            root = Path(os.path.expanduser("~")) / ".poindexter" / "video"
    root.mkdir(parents=True, exist_ok=True)
    return root


def output_paths(
    spec: StitchSpec,
    task_id: str,
) -> tuple[Path, str]:
    """Return ``(output_dir, output_path)`` for the stitch.

    Files land at ``<root>/<task_id>_<format>.mp4`` so debugging is
    easy and the upload Stage can find them by convention.
    """
    root = _video_output_root()
    return root, str(root / spec.output_filename.format(task_id=task_id))


# ---------------------------------------------------------------------------
# media_assets persistence
# ---------------------------------------------------------------------------


async def persist_media_asset(
    *,
    pool: Any,
    post_id: Any,
    asset_type: str,
    provider_plugin: str,
    output_path: str,
    public_url: str,
    width: int,
    height: int,
    duration_ms: int,
    file_size_bytes: int,
    cost_usd: float,
    electricity_kwh: float,
    metadata: dict[str, Any],
) -> str | None:
    """Insert a row in ``media_assets`` for the rendered video.

    Returns the new row's UUID as a string, or ``None`` on any DB
    failure — write failures must NEVER raise out of a Stage; the
    pipeline continues and the operator sees the warning in logs.
    """
    if pool is None:
        logger.warning(
            "[video.stitch] no DB pool — media_assets row not persisted",
        )
        return None
    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO media_assets (
                    type, source, storage_provider, url, storage_path,
                    metadata, post_id, provider_plugin,
                    width, height, duration_ms, file_size_bytes,
                    mime_type, cost_usd, electricity_kwh
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6::jsonb, $7, $8,
                    $9, $10, $11, $12,
                    $13, $14, $15
                )
                RETURNING id
                """,
                asset_type,
                "pipeline",
                "local",
                public_url or "",
                output_path,
                _json_dumps(metadata),
                post_id,
                provider_plugin,
                width,
                height,
                duration_ms,
                file_size_bytes,
                "video/mp4",
                cost_usd,
                electricity_kwh,
            )
            return str(row_id) if row_id else None
    except Exception as exc:
        logger.warning("[video.stitch] media_assets INSERT failed: %s", exc)
        return None


def _json_dumps(payload: dict[str, Any]) -> str:
    """Defensive JSON dump for the metadata column.

    asyncpg serializes dicts when the column is jsonb, but providers
    sometimes stash non-JSON-serializable values (Path, set) in
    metadata. Coerce to str on failure so the row insert still
    succeeds.
    """
    import json

    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return json.dumps({k: str(v) for k, v in payload.items()})


# ---------------------------------------------------------------------------
# R2 upload — best-effort; pipeline still produces a local file on failure
# ---------------------------------------------------------------------------


async def upload_to_object_storage(
    *,
    local_path: str,
    asset_type: str,
    post_id: Any,
    site_config: Any,
) -> str:
    """Push the rendered video to the configured object store.

    Returns the public URL on success, empty string on failure (Stage
    keeps the local file and writes the empty URL to media_assets so
    the operator can reupload manually if needed).
    """
    if not local_path or not os.path.exists(local_path) or site_config is None:
        return ""
    try:
        from services.r2_upload_service import upload_to_r2
    except Exception as exc:
        logger.warning("[video.stitch] r2 upload import failed: %s", exc)
        return ""

    # Object key: video/<type>/<post_id>_<random>.mp4 — random suffix
    # avoids cache-poisoning if the same post is re-rendered.
    suffix = uuid4().hex[:8]
    object_key = f"video/{asset_type}/{post_id}_{suffix}.mp4"
    try:
        url = await upload_to_r2(
            local_path,
            object_key,
            content_type="video/mp4",
            site_config=site_config,
        )
    except Exception as exc:
        logger.warning("[video.stitch] r2 upload raised: %s", exc)
        return ""
    return str(url) if url else ""
