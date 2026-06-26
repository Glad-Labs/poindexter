"""content.inject_images — replace [IMAGE-N] markers with <img> tags.

Takes content + image_results (from content.generate_images) and replaces
each [IMAGE-N] placeholder with the appropriate HTML <img> tag.
Runs _cleanup_leaked_descriptions + _normalize_from_router afterward.
Writes stages["2c_inline_images_replaced"] to DB via database_service.

Produces: content (with images injected), inline_images_replaced (count).

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.inject_images",
    type="atom",
    version="1.0.0",
    description=(
        "Replace [IMAGE-N] placeholders with <img> tags using image_results. "
        "Cleans up leaked image-gen descriptions. Persists updated content to DB."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body with [IMAGE-N] markers"),
        FieldSpec(name="image_results", type="list", description="[{num, url, alt_text, source}, ...]"),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="database_service", type="object", description="DB service"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="content with images injected"),
        FieldSpec(name="inline_images_replaced", type="int", description="count of images injected"),
    ),
    requires=("content", "image_results", "task_id"),
    produces=("content", "inline_images_replaced"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Inject images and clean up the content."""
    from modules.content.stages.replace_inline_images import (
        _cleanup_leaked_descriptions,
        _inject_html_image,
        _normalize_from_router,
    )

    content_text = state.get("content") or ""
    image_results = state.get("image_results") or []
    task_id = state.get("task_id")
    database_service = state.get("database_service")

    replaced_count = 0
    for result in image_results:
        num = str(result.get("num", ""))
        img_url = result.get("url")
        alt_text = result.get("alt_text", "")
        source = result.get("source", "none")

        if not img_url or not num:
            # Remove placeholder — no image available.
            content_text = re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
            continue

        if source == "image_gen":
            content_text = _inject_html_image(
                content_text, num, img_url, alt_text, width=1024, height=1024,
            )
            replaced_count += 1
            logger.info("  [IMAGE-%s] image_gen injected", num)
        elif source == "pexels":
            photographer = alt_text.replace("Photo by ", "").strip()
            pexels_html = (
                f'\n\n<img src="{img_url}" alt="{alt_text}" '
                f'width="650" height="433" loading="lazy" />\n'
                f'<figcaption>Photo by {photographer} on Pexels</figcaption>\n\n'
            )
            content_text = re.sub(
                rf"\[IMAGE-{num}[^\]]*\]", pexels_html, content_text, count=1,
            )
            replaced_count += 1
            logger.info("  [IMAGE-%s] Pexels injected", num)
        else:
            # Strip unresolved placeholder.
            content_text = re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)

    content_text = _cleanup_leaked_descriptions(content_text)
    content_text = _normalize_from_router(content_text)

    # Persist image-populated content to DB.
    if task_id and database_service is not None:
        try:
            await database_service.update_task(
                task_id=task_id, updates={"content": content_text},
            )
        except Exception as e:
            logger.warning("[content.inject_images] DB update failed (non-critical): %s", e)

    stages = state.get("stages") or {}
    stages["2c_inline_images_replaced"] = replaced_count > 0

    logger.info("Replaced %d inline images in content", replaced_count)
    return {
        "content": content_text,
        "inline_images_replaced": replaced_count,
        "stages": stages,
    }


__all__ = ["ATOM_META", "run"]
