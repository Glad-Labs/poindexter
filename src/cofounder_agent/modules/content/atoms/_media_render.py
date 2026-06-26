"""Shared render helper for the Stage-2 media render atoms (Plan 4).

Underscore-prefixed so the atom-registry filesystem scan SKIPS it
(``services/atom_registry.py``: files starting with ``_`` are not
discovered as atoms). This is plumbing, not an atom — the two thin atoms
``media.render_long_video`` / ``media.render_short_video`` both delegate
here so the wiring of the EXISTING render engine into graph state lives
in one place.

What it does:

1. Reads a shot-list dict from state (``video_shot_list`` for the long
   form, ``short_shot_list`` for the 9:16 short) and rehydrates it into a
   ``VideoShotList``. A missing / None / malformed shot list is a graceful
   no-op — nothing to render, so the atom emits an empty output key rather
   than failing the graph.
2. Resolves the aspect profile (``9:16`` → 1080×1920, else 1920×1080) and
   threads the podcast narration + the ambient bed (#679) into the
   existing ``render_shot_list`` engine.
3. On a partial render (some-but-not-all shots) emits a ``partial_render``
   finding so a degraded video never ships silently (redesign §9).
4. On render failure emits a ``render_failed`` finding and returns empty —
   it NEVER raises, because a render failure must not halt the graph.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

from pydantic import ValidationError

from schemas.video_shot_list import VideoShotList
from services.gpu_scheduler import gpu
from services.video_renderers.shot_list_renderer import render_shot_list
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# image-gen URL convention (mirrors source_featured_image.py / the shot renderer).
_DEFAULT_IMAGE_GEN_URL = "http://host.docker.internal:9836"


def _resolve_dims(aspect: str) -> tuple[int, int]:
    """Aspect profile → (width, height). 9:16 short vs 16:9 long-form."""
    if aspect == "9:16":
        return 1080, 1920
    return 1920, 1080


async def render_from_state(
    state: dict[str, Any],
    *,
    shot_list_key: str,
    output_key: str,
    narration_key: str = "podcast_audio_path",
    caption_key: str = "caption_srt_path",
) -> dict[str, Any]:
    """Render a video from a shot-list channel in graph state.

    Args:
        state: The LangGraph pipeline state dict.
        shot_list_key: Which state channel holds the shot-list dict
            (``video_shot_list`` or ``short_shot_list``).
        output_key: Which state channel to write the rendered path into
            (``long_video_path`` or ``short_video_path``). MUST be a
            declared ``PipelineState`` channel or LangGraph drops it.
        narration_key: Which state channel holds this lane's narration audio
            path (``long_narration_audio_path`` / ``short_narration_audio_path``
            per #689). Defaults to ``podcast_audio_path`` for backcompat.
        caption_key: Which state channel holds this lane's burned-in SRT
            (``long_caption_srt_path`` / ``short_caption_srt_path``). Defaults to
            ``caption_srt_path`` for backcompat.

    Returns:
        ``{output_key: <path-or-empty-string>}`` — empty on no-op /
        invalid input / render failure. Never raises.
    """
    shot_list_dict = state.get(shot_list_key)
    if not shot_list_dict:
        # Nothing persisted (e.g. a non-media task, or the director never
        # produced this aspect) — graceful no-op, render NOT attempted.
        logger.info(
            "[media.render] no %s in state — skipping %s render",
            shot_list_key,
            output_key,
        )
        return {output_key: ""}

    try:
        shot_list = VideoShotList.model_validate(shot_list_dict)
    except ValidationError as exc:
        logger.warning(
            "[media.render] %s failed VideoShotList validation: %s — skipping",
            shot_list_key,
            exc,
        )
        return {output_key: ""}

    task_id = state.get("task_id")
    site_config = state.get("site_config")
    image_gen_url = (
        site_config.get("image_gen_server_url", _DEFAULT_IMAGE_GEN_URL)
        if site_config is not None
        else _DEFAULT_IMAGE_GEN_URL
    )

    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )

    narration = state.get(narration_key) or ""
    ambient = state.get("video_ambient_audio_path") or None
    # SRT caption track produced by media.transcribe_narration (per-lane #689).
    # Empty-string is the atom's no-op sentinel, so `or None` maps it to None —
    # no track to burn.
    caption = state.get(caption_key) or None
    width, height = _resolve_dims(shot_list.aspect)

    out_path = f"{tempfile.gettempdir()}/media_{task_id}_{output_key}.mp4"

    try:
        # Hold the GPU for the whole render. The render drives wan + image-gen over
        # HTTP and never went through the scheduler before (validation findings
        # 4b/7): the ~18GB writer/director stayed resident in Ollama and starved
        # wan+image-gen → "inference server unreachable" → render failures. The
        # "video" owner evicts Ollama on acquire, and the cross-process
        # pg_advisory_lock blocks the content pipeline (prefect-worker) for the
        # render's duration so they can't oversubscribe the 32GB card.
        async with gpu.lock(
            "video",
            model="shot_list_render",
            task_id=str(task_id or "") or None,
            phase="media_render",
        ):
            result = await render_shot_list(
                post_id=str(task_id or ""),
                shot_list=shot_list,
                audio_path=narration,
                output_path=out_path,
                image_gen_url=image_gen_url,
                site_config=site_config,
                pool=pool,
                width=width,
                height=height,
                ambient_path=ambient,
                caption_path=caption,
            )
    except Exception as exc:  # noqa: BLE001 — a render must never halt the graph
        logger.exception("[media.render] %s render raised: %s", output_key, exc)
        emit_finding(
            source="media.render_video",
            kind="render_failed",
            title=f"{output_key}: render raised an exception",
            body=f"render_shot_list raised for task {task_id}: {exc}",
            severity="warn",
            dedup_key=f"render_failed:{task_id}:{output_key}",
            extra={"task_id": str(task_id or ""), "output_key": output_key},
        )
        return {output_key: ""}

    if not result.success:
        emit_finding(
            source="media.render_video",
            kind="render_failed",
            title=f"{output_key}: render failed",
            body=(
                f"render_shot_list returned success=False for task {task_id}: "
                f"{result.error or 'no error detail'}"
            ),
            severity="warn",
            dedup_key=f"render_failed:{task_id}:{output_key}",
            extra={"task_id": str(task_id or ""), "output_key": output_key},
        )
        return {output_key: ""}

    if result.shots_rendered < result.shots_total:
        # A degraded video (e.g. 3 of 8 shots) would otherwise ship
        # silently — surface it for triage (redesign §9 / Gap C).
        emit_finding(
            source="media.render_video",
            kind="partial_render",
            title=(
                f"{output_key}: {result.shots_rendered}/{result.shots_total} "
                "shots rendered"
            ),
            body=(
                f"Only {result.shots_rendered} of {result.shots_total} shots "
                f"rendered for task {task_id}; a degraded video shipped. "
                "Check the per-shot video_shot_rendered audit_log rows for "
                "which sources failed."
            ),
            severity="warn",
            dedup_key=f"partial_render:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shots_rendered": result.shots_rendered,
                "shots_total": result.shots_total,
            },
        )

    return {output_key: result.output_path or ""}


__all__ = ["render_from_state"]
