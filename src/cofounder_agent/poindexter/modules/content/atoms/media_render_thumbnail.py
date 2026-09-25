"""media.render_thumbnail — compose the long-form video's custom YouTube thumbnail.

Runs after the renders and before media QA, so the thumbnail is persisted
with the video and reviewed with it: nothing goes public without the
operator's sign-off, and the thumbnail is the most visible thing a YouTube
upload has. ``media.persist`` moves it into the durable media dir as a
``video_thumbnail`` asset; ``media_distribute`` hands it to the YouTube
adapter with the long upload.

All of the work lives in ``services/video_thumbnail.compose_video_thumbnail``
(background, hook text, render); this atom only adapts graph state to it.
Fail-soft: no long video, the feature switched off, or a render failure each
produce ``long_thumbnail_path = ""``, and the upload falls back to YouTube's
own frame exactly as before.

NOTE (#674 trap): ``long_thumbnail_path`` and ``long_thumbnail_meta`` MUST be
declared ``PipelineState`` channels (they are) or LangGraph drops them.
"""

from __future__ import annotations

import json
import os
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

ATOM_META = AtomMeta(
    name="media.render_thumbnail",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: compose the long-form video's custom YouTube thumbnail "
        "(text-free background + hook text in real type + brand mark)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="long_video_path", type="str", description="rendered 16:9 MP4 (frame backgrounds)", required=False),
        FieldSpec(name="video_shot_list", type="dict", description="16:9 shot list (presenter window)", required=False),
        FieldSpec(name="video_long_script", type="str", description="narration text (grounds numbers in the hook)", required=False),
        FieldSpec(name="niche_slug", type="str", description="niche slug ('' when none) — resolves the presenter persona", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (video_thumbnail_* settings)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
    ),
    outputs=(
        FieldSpec(name="long_thumbnail_path", type="str", description="composed JPEG path ('' on no-op/failure)"),
        FieldSpec(name="long_thumbnail_meta", type="dict", description="hook text + background source, stored on the asset row", required=False),
    ),
    requires=("task_id",),
    produces=("long_thumbnail_path",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Compose the thumbnail for this task's long-form video."""
    from poindexter.services.video_thumbnail import compose_video_thumbnail

    task_id = str(state.get("task_id") or "")
    video_path = str(state.get("long_video_path") or "")
    if not task_id or not video_path or not os.path.exists(video_path):
        # The thumbnail belongs to the long upload; no long video, no thumbnail.
        return {"long_thumbnail_path": ""}
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    shot_list = state.get("video_shot_list")
    if isinstance(shot_list, str):
        try:
            shot_list = json.loads(shot_list)
        except json.JSONDecodeError:
            shot_list = None
    try:
        result = await compose_video_thumbnail(
            task_id=task_id,
            pool=pool,
            site_config=state.get("site_config"),
            video_path=video_path,
            shot_list=shot_list if isinstance(shot_list, dict) else None,
            source_text=str(state.get("video_long_script") or ""),
            niche_slug=str(state.get("niche_slug") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001 — a thumbnail must never cost the video
        from poindexter.utils.exception_format import describe_exception

        logger.warning(
            "[media.render_thumbnail] task %s: thumbnail failed (%s) — the upload "
            "keeps YouTube's own frame", task_id, describe_exception(exc),
        )
        return {"long_thumbnail_path": ""}
    if result is None:
        return {"long_thumbnail_path": ""}
    return {
        "long_thumbnail_path": result.path,
        "long_thumbnail_meta": {
            "hook": result.hook,
            "hook_note": result.hook_note,
            "background": result.background,
            "size_bytes": result.size_bytes,
        },
    }
