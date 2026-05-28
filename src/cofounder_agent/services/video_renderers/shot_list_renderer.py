"""ShotListRenderer — assemble a video from a VideoShotList.

The shot list (``schemas/video_shot_list.VideoShotList``) is the seam
between the LLM director (decides what each shot should be) and this
renderer (assembles a final MP4 from per-shot clips + the podcast
narration). The shot list lives on ``posts.video_shot_list jsonb`` —
NULL means "director hasn't run, fall back to legacy slideshow".

Per-source dispatch:

- ``sdxl`` — single SDXL frame, held static for ``duration_s``
- ``sdxl_kenburns`` — single SDXL frame with Ken Burns motion
- ``pexels`` — Pexels stock image (image, not video — pexels-video
  is a future provider). Falls through to SDXL with the query as
  prompt when no Pexels API key is configured. Ken Burns disabled
  (real photos with motion look fine static for shorter shots).
- ``wan21`` — Wan2.1 T2V model clip via ``Wan21Provider``. Capped at
  6 seconds per the director prompt (artifacts beyond that show
  seams in the diffusion).
- ``holdover`` — cross-fade transition placeholder. V1 treats this
  the same as ``sdxl`` by carrying the prior shot's prompt — a true
  cross-fade filtergraph is a follow-up.

Concat happens via ``FFmpegLocalCompositor`` (the existing
``MediaCompositor`` plugin). Each per-shot clip becomes a
``CompositionScene``; the compositor handles normalization, Ken Burns
zoompan for stills, and the narration audio mix.

Every shot's render result lands in ``audit_log`` as a
``video_shot_rendered`` event so operators can see which clips
succeeded without grepping container logs.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugins.media_compositor import CompositionRequest, CompositionScene
from schemas.video_shot_list import Shot, VideoShotList

logger = logging.getLogger(__name__)


# Wan2.1 clip-duration cap. Artifacts beyond ~6s show seams in the
# 1.3B model's output; the director prompt also caps shot duration at
# 6s for wan21. Defensive ceiling here in case the director sneaks
# something through.
_WAN21_MAX_DURATION_S = 6


@dataclass
class ShotRenderResult:
    """Outcome of rendering one shot."""

    idx: int
    source: str
    success: bool
    clip_path: str | None = None
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class ShotListRenderResult:
    """Outcome of a full shot-list render."""

    success: bool
    output_path: str | None = None
    file_size_bytes: int = 0
    duration_s: float = 0.0
    shots_rendered: int = 0
    shots_total: int = 0
    error: str | None = None


async def _log_shot_audit(
    pool: Any,
    *,
    post_id: str,
    shot_result: ShotRenderResult,
) -> None:
    """Best-effort audit_log insert for a single shot's render result.

    Operator-visible in the audit-log dashboard so the per-source
    success rate can be monitored without grepping container logs.
    Failures here MUST NOT take the render down.
    """
    if pool is None:
        return
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, 'shot_list_renderer', $2::jsonb, $3)
            """,
            "video_shot_rendered",
            json.dumps({
                "post_id": post_id,
                "shot_idx": shot_result.idx,
                "source": shot_result.source,
                "success": shot_result.success,
                "duration_s": shot_result.duration_s,
                "error": shot_result.error,
            }),
            "info" if shot_result.success else "warning",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[SHOT_LIST] audit_log insert failed for shot %d: %s",
            shot_result.idx, exc,
        )


async def _render_sdxl_image(
    *,
    prompt: str,
    output_path: str,
    sdxl_url: str,
    http_client_factory: Any,
) -> bool:
    """Render one SDXL image to disk via the SDXL server.

    Mirrors the shape used in
    ``video_service._generate_images_from_scenes`` but writes to a
    caller-supplied path. Returns True when the PNG is on disk.
    """
    import httpx

    from services.video_service import _consume_sdxl_image_response

    neg = (
        "text, words, letters, watermark, face, person, hands, blurry, "
        "low quality, distorted, ugly, deformed"
    )
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(60.0, connect=5.0),
        ) as client:
            resp = await client.post(
                f"{sdxl_url}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": neg,
                    "steps": 4,
                    "guidance_scale": 1.0,
                },
                timeout=60,
            )
            got = await _consume_sdxl_image_response(
                resp,
                sdxl_url=sdxl_url,
                output_path=output_path,
                frame_label=f"shot SDXL {os.path.basename(output_path)}",
            )
            return got is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] SDXL render failed for %s: %s",
            os.path.basename(output_path), exc,
        )
        return False


async def _render_wan21_clip(
    *,
    prompt: str,
    output_path: str,
    duration_s: int,
    site_config: Any,
) -> bool:
    """Render one Wan2.1 T2V clip to ``output_path``.

    Delegates to the existing ``Wan21Provider`` so the request body
    shape (``prompt`` / ``negative_prompt`` / ``steps`` /
    ``guidance_scale`` / ``duration_s`` / ``width`` / ``height`` /
    ``fps`` / ``model``) is the correct one for the wan-server.
    Returns True on success.
    """
    from services.video_providers.wan2_1 import Wan21Provider

    provider = Wan21Provider()
    try:
        results = await provider.fetch(
            prompt,
            {
                "output_path": output_path,
                "duration_s": min(duration_s, _WAN21_MAX_DURATION_S),
                "_site_config": site_config,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] Wan21 render raised for %s: %s",
            os.path.basename(output_path), exc,
        )
        return False

    if not results:
        return False
    return bool(results[0].file_path) and os.path.exists(results[0].file_path)


async def _render_one_shot(
    shot: Shot,
    *,
    prior_clip: str | None,
    work_dir: Path,
    sdxl_url: str,
    site_config: Any,
    http_client_factory: Any,
) -> ShotRenderResult:
    """Produce a clip file for one shot.

    Returns a ``ShotRenderResult`` with ``clip_path`` set on success.
    Holdover shots reuse ``prior_clip`` (V1 simplification — a true
    cross-fade is a follow-up).
    """
    source = shot.source

    if source == "holdover":
        if not prior_clip:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="holdover shot at idx=0 has no prior clip to carry",
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=prior_clip,
            duration_s=shot.duration_s,
        )

    if source in ("sdxl", "sdxl_kenburns", "pexels"):
        # Pexels v1 fallback: use the search query as the SDXL prompt.
        # A dedicated Pexels stock provider with API key support is a
        # follow-up; until then the shot still renders rather than
        # failing the whole video.
        sdxl_prompt = shot.prompt if shot.prompt else (shot.query or "")
        if not sdxl_prompt:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error=f"{source} shot missing prompt and query",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.png")
        ok = await _render_sdxl_image(
            prompt=sdxl_prompt,
            output_path=clip_path,
            sdxl_url=sdxl_url,
            http_client_factory=http_client_factory,
        )
        if not ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="SDXL render returned no image",
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=clip_path,
            duration_s=shot.duration_s,
        )

    if source == "wan21":
        if not shot.prompt:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="wan21 shot missing prompt",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.mp4")
        ok = await _render_wan21_clip(
            prompt=shot.prompt,
            output_path=clip_path,
            duration_s=int(shot.duration_s),
            site_config=site_config,
        )
        if not ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="Wan21 render returned no clip",
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=clip_path,
            duration_s=shot.duration_s,
        )

    return ShotRenderResult(
        idx=shot.idx,
        source=source,
        success=False,
        error=f"unknown source {source!r}",
    )


async def render_shot_list(
    *,
    post_id: str,
    shot_list: VideoShotList,
    audio_path: str,
    output_path: str,
    sdxl_url: str,
    site_config: Any,
    pool: Any = None,
    http_client_factory: Any = None,
) -> ShotListRenderResult:
    """Render a full video from a shot list.

    Args:
        post_id: For audit_log + temp-dir naming.
        shot_list: Validated ``VideoShotList`` from
            ``posts.video_shot_list`` (or in-memory).
        audio_path: Local path to the podcast narration MP3 (ideally
            the body-only sibling — see
            ``video_service.generate_video_for_post``).
        output_path: Where to write the final MP4.
        sdxl_url: Base URL of the SDXL inference server.
        site_config: DI seam — required for Wan2.1 provider config.
        pool: asyncpg pool for audit-log inserts. Optional.
        http_client_factory: ``httpx.AsyncClient`` factory — defaults
            to the real client. Tests inject a mock.

    Returns:
        ``ShotListRenderResult`` with file path on success.
    """
    if http_client_factory is None:
        import httpx
        http_client_factory = httpx.AsyncClient

    # Per-shot temp dir. Cleaned up unless the operator wants to
    # forensic-debug a failed render (kept on disk on exception).
    work_dir = Path(
        tempfile.mkdtemp(prefix=f"shotlist_{post_id}_", suffix=""),
    )
    logger.info(
        "[SHOT_LIST] rendering post_id=%s — %d shots, total=%.1fs, work_dir=%s",
        post_id, len(shot_list.shots), shot_list.total_duration_s, work_dir,
    )

    shot_results: list[ShotRenderResult] = []
    prior_clip: str | None = None
    for shot in shot_list.shots:
        result = await _render_one_shot(
            shot,
            prior_clip=prior_clip,
            work_dir=work_dir,
            sdxl_url=sdxl_url,
            site_config=site_config,
            http_client_factory=http_client_factory,
        )
        await _log_shot_audit(pool, post_id=post_id, shot_result=result)
        shot_results.append(result)
        if result.success and result.clip_path:
            prior_clip = result.clip_path

    rendered = [r for r in shot_results if r.success and r.clip_path]
    if not rendered:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=0,
            error="no shots rendered — director output unrenderable",
        )

    # Build CompositionScenes. The compositor handles Ken Burns for
    # stills (per-scene zoompan via ``ken_burns_enabled``); we pass
    # narration ONLY on shot 0 so the entire podcast audio plays as
    # one stream over the visual concat. (Per-shot narration slicing
    # is a follow-up — the schema's ``narration_offset_s`` field is
    # the seam.)
    scenes: list[CompositionScene] = []
    for r in rendered:
        scenes.append(
            CompositionScene(
                clip_path=r.clip_path or "",
                # All scenes share the same audio source — only the
                # first scene gets the narration_path; the rest get
                # None (silent), and the compositor's soundtrack-mix
                # layers the audio over the concatenated video.
                narration_path=audio_path if r.idx == rendered[0].idx else None,
                duration_s=float(r.duration_s),
            ),
        )

    request = CompositionRequest(
        scenes=scenes,
        soundtrack_path=audio_path,
        output_path=output_path,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        container="mp4",
        metadata={
            "post_id": post_id,
            "renderer": "shot_list_renderer",
            "shot_count": len(rendered),
            "director_model": shot_list.director_model,
            "director_prompt_version": shot_list.director_prompt_version,
        },
    )

    from services.media_compositors.ffmpeg_local import FFmpegLocalCompositor

    compositor = FFmpegLocalCompositor(site_config=site_config)
    composition = await compositor.compose(request)

    if not composition.success or not composition.output_path:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=len(rendered),
            error=(
                f"compositor failed: {composition.error or 'no output_path'}"
            ),
        )

    return ShotListRenderResult(
        success=True,
        output_path=composition.output_path,
        file_size_bytes=composition.file_size_bytes,
        duration_s=composition.duration_s,
        shots_rendered=len(rendered),
        shots_total=len(shot_list.shots),
    )
