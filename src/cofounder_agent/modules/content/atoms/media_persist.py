"""media.persist — durably store the rendered videos + record media_assets.

Stage-2 terminal node (epic poindexter#689, Plan 8). The render atoms write the
long/short MP4s to the OS temp dir (``{tmpdir}/media_{task_id}_{output_key}.mp4``)
and surface their paths on the ``long_video_path`` / ``short_video_path``
channels. Temp files don't survive to the post-Gate-2 distribution pass, so this
node moves them into the durable media dir (``VIDEO_DIR = ~/.poindexter/video``)
and records a ``media_assets`` row for each via the canonical
:func:`services.media_asset_recorder.record_media_asset` writer (don't reinvent
the wheel — that helper is the single source of truth for media_assets writes,
Glad-Labs/poindexter#161).

**Task-keyed, post-deferred.** The rows are stamped with ``task_id`` and
``post_id=None``: at Stage-2 time the ``posts`` row may not exist yet (it's
created at publish). The post-keyed Gate-2 approval + distribution lane resolves
the post later via ``posts.metadata->>'pipeline_task_id'`` (Plan 8 / 8b-2). This
split keeps the post dependency out of the render graph.

**Best-effort.** A missing render (the renders emit their own findings on
failure) or a no-pool environment never raises — the node returns the list of
recorded asset ids and lets the graph finish.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.media_asset_recorder import record_media_asset

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.persist",
    type="atom",
    version="1.0.0",
    description=(
        "Move the rendered long/short videos out of temp into the durable "
        "media dir and record task-keyed media_assets rows (post resolved "
        "later at distribution time)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="long_video_path", type="str", description="temp path of the 16:9 render", required=False),
        FieldSpec(name="short_video_path", type="str", description="temp path of the 9:16 render", required=False),
        FieldSpec(name="video_shot_list", type="object", description="long shot-list (dims/duration)", required=False),
        FieldSpec(name="short_shot_list", type="object", description="short shot-list (dims/duration)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool seam)", required=False),
    ),
    outputs=(
        FieldSpec(name="media_assets_recorded", type="list", description="ids of the media_assets rows written"),
    ),
    requires=("task_id",),
    produces=("media_assets_recorded",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("db_write", "file_move"),
    retry=RetryPolicy(),
    parallelizable=False,
)

# (state path channel, shot-list channel, media_assets.type, durable suffix,
#  default aspect when the shot-list is absent). The long video keeps the bare
#  ``{task_id}.mp4`` name (matching the legacy VIDEO_DIR convention); the short
#  gets a ``_short`` suffix so both live side-by-side under one task id.
_TARGETS: tuple[tuple[str, str, str, str, str], ...] = (
    ("long_video_path", "video_shot_list", "video_long", "", "16:9"),
    ("short_video_path", "short_shot_list", "video_short", "_short", "9:16"),
)


def _extract(shot_list: Any, key: str) -> Any:
    """Read ``key`` off a shot-list that may be a dict (JSON) or an object."""
    if shot_list is None:
        return None
    if isinstance(shot_list, dict):
        return shot_list.get(key)
    return getattr(shot_list, key, None)


def _dims_for_aspect(aspect: Any) -> tuple[int, int]:
    """Pixel dims for the render aspect — 9:16 vertical else 16:9 (1080p)."""
    return (1080, 1920) if str(aspect) == "9:16" else (1920, 1080)


def _duration_ms(shot_list: Any) -> int | None:
    total = _extract(shot_list, "total_duration_s")
    try:
        return int(float(total) * 1000) if total else None
    except (TypeError, ValueError):
        return None


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Move temp renders to durable storage + record media_assets rows."""
    task_id = state.get("task_id")
    if not task_id:
        raise ValueError("media.persist requires task_id")

    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if pool is None:
        # No DB pool → we can't record media_assets rows. Moving the renders
        # without a row would strand task-keyed orphan files the post-keyed
        # reconciliation watchdog can't heal, so skip the whole op. The real
        # Stage-2 dispatcher always supplies a pool; this is a defensive guard.
        logger.warning(
            "[media.persist] no DB pool for task %s — skipping persist", task_id,
        )
        return {"media_assets_recorded": []}

    # Durable media dir — imported lazily so this light atom doesn't pull in
    # video_service's generation deps at module load (and so tests can patch
    # services.video_service.VIDEO_DIR).
    from services.video_service import VIDEO_DIR

    recorded: list[str] = []
    for path_key, shot_key, asset_type, suffix, default_aspect in _TARGETS:
        src = state.get(path_key) or ""
        if not src or not os.path.exists(src):
            # The render atom already emitted a finding on failure; an absent
            # path here just means "this flavor wasn't produced this run".
            continue

        durable = VIDEO_DIR / f"{task_id}{suffix}.mp4"
        try:
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(src, durable)
        except OSError as exc:
            logger.warning(
                "[media.persist] move failed for %s (%s): %s",
                asset_type, src, exc,
            )
            continue

        shot_list = state.get(shot_key)
        width, height = _dims_for_aspect(_extract(shot_list, "aspect") or default_aspect)
        try:
            size = os.path.getsize(durable)
        except OSError:
            size = 0

        asset_id = await record_media_asset(
            pool=pool,
            post_id=None,  # resolved later at distribution (Plan 8 / 8b-2)
            task_id=task_id,
            asset_type=asset_type,
            storage_path=str(durable),
            storage_provider="local",
            source="pipeline",
            provider_plugin="compositor.ffmpeg_local",
            width=width,
            height=height,
            duration_ms=_duration_ms(shot_list),
            file_size_bytes=size,
        )
        if asset_id:
            recorded.append(asset_id)
            logger.info(
                "[media.persist] recorded %s media_asset %s for task %s (%s)",
                asset_type, asset_id, task_id, durable,
            )

    return {"media_assets_recorded": recorded}


__all__ = ["ATOM_META", "run"]
