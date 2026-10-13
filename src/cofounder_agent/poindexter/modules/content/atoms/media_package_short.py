"""media.package_short — the Short's YouTube title, description lead and vertical thumbnail.

Runs after ``media.render_thumbnail`` and before media QA, so the package is
persisted with the Short and reviewed with it in the media drawer, the same
way the long video's thumbnail is. ``media.persist`` stores the title and
description on the ``video_short`` asset row (``metadata.youtube_package``)
and the thumbnail as a ``video_short_thumbnail`` asset; ``media_distribute``
uploads exactly what was stored.

The work lives in services: ``services/short_package.compose_short_package``
writes and checks the title and description; ``services/video_thumbnail.
compose_short_thumbnail`` lays the long thumbnail's hook over a vertical
frame. This atom only adapts graph state to them.

Fail-soft: no Short, a feature switched off, or a failure each leave that
part empty, and the upload falls back to what every Short shipped with before
(the narration-derived title and opener, YouTube's own frame).

NOTE (#674 trap): ``short_package``, ``short_thumbnail_path`` and
``short_thumbnail_meta`` MUST be declared ``PipelineState`` channels (they
are) or LangGraph drops them.
"""

from __future__ import annotations

import os
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

ATOM_META = AtomMeta(
    name="media.package_short",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: write and check the Short's own YouTube title and description "
        "lead, and compose its vertical custom thumbnail from the long "
        "thumbnail's hook."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="short_video_path", type="str", description="rendered 9:16 MP4 (frame backgrounds)", required=False),
        FieldSpec(name="short_summary_script", type="str", description="the Short's narration (what the title is written from)", required=False),
        FieldSpec(name="long_thumbnail_meta", type="dict", description="the long thumbnail's hook, reused on the Short", required=False),
        FieldSpec(name="niche_slug", type="str", description="niche slug ('' when none) — resolves the presenter persona", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (youtube_short_* / video_short_thumbnail_* settings)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
    ),
    outputs=(
        FieldSpec(name="short_package", type="dict", description="title + description lead and their notes ({} on no-op)"),
        FieldSpec(name="short_thumbnail_path", type="str", description="composed vertical JPEG path ('' on no-op/failure)", required=False),
        FieldSpec(name="short_thumbnail_meta", type="dict", description="hook text + background source, stored on the asset row", required=False),
    ),
    requires=("task_id",),
    produces=("short_package",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=True,
)


def _describe(exc: Exception) -> str:
    from poindexter.utils.exception_format import describe_exception

    return describe_exception(exc)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Package this task's Short for YouTube."""
    from poindexter.services.short_package import compose_short_package
    from poindexter.services.video_thumbnail import compose_short_thumbnail

    task_id = str(state.get("task_id") or "")
    video_path = str(state.get("short_video_path") or "")
    if not task_id or not video_path or not os.path.exists(video_path):
        # The package belongs to the Short's upload; no Short, no package.
        return {"short_package": {}, "short_thumbnail_path": ""}
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    site_config = state.get("site_config")
    out: dict[str, Any] = {"short_package": {}, "short_thumbnail_path": ""}

    script = state.get("short_summary_script")
    try:
        package = await compose_short_package(
            pool=pool, site_config=site_config, task_id=task_id,
            script=script if isinstance(script, str) and script.strip() else None,
        )
        out["short_package"] = package.as_metadata()
    except Exception as exc:  # noqa: BLE001 — a title must never cost the Short
        logger.warning(
            "[media.package_short] task %s: package failed (%s) — the upload keeps the "
            "narration-derived title", task_id, _describe(exc),
        )

    meta = state.get("long_thumbnail_meta")
    hook = meta.get("hook") if isinstance(meta, dict) else None
    try:
        thumb = await compose_short_thumbnail(
            task_id=task_id, pool=pool, site_config=site_config, video_path=video_path,
            hook_text=str(hook) if hook is not None else None,
            niche_slug=str(state.get("niche_slug") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001 — a thumbnail must never cost the Short
        logger.warning(
            "[media.package_short] task %s: Short thumbnail failed (%s) — the upload "
            "keeps YouTube's own frame", task_id, _describe(exc),
        )
        thumb = None
    if thumb is not None:
        out["short_thumbnail_path"] = thumb.path
        out["short_thumbnail_meta"] = {
            "hook": thumb.hook,
            "hook_note": thumb.hook_note,
            "background": thumb.background,
            "size_bytes": thumb.size_bytes,
        }
    return out
