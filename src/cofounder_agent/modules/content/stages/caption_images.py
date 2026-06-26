"""CaptionImagesStage — re-caption inline + featured images with vision.

Runs after ``source_featured_image`` (so all images exist). Reads every
``<img alt="...">`` in the draft and replaces the alt with a pixel-accurate
caption from :func:`services.image_captioner.caption_image`. Fail-soft per
image: a ``None`` caption leaves the existing alt untouched. Also re-captions
the featured image into ``featured_image_alt``. No-op when
``vision_alt_enabled`` is false (kill switch).

Why this stage exists: legacy alt text was derived from the writer's
``[IMAGE-N: <description>]`` placeholder — the generation *intent*, not the
rendered pixels. image-gen produces abstract editorial art (no people/faces), so
those alts routinely describe content the image doesn't contain. This stage
fixes it forward at generation time; ``scripts/backfill-image-alt-vision.py``
fixes existing posts. Both share :mod:`services.image_captioner`.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from plugins.stage import StageResult
from services.alt_text import _IMG_ALT_RE  # (<img...alt=")(value)(")
from services.image_captioner import caption_image

logger = logging.getLogger(__name__)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.IGNORECASE)


def _enabled(site_config: Any) -> bool:
    if site_config is None:
        return True
    raw = str(site_config.get("vision_alt_enabled", "true") or "true").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _clean_for_attr(alt: str) -> str:
    """Make a caption safe to inject into ``alt="..."`` — no raw double quotes."""
    return alt.replace('"', "'").strip()


class CaptionImagesStage:
    name = "caption_images"
    description = "Re-caption inline + featured images with qwen3-vl (accurate alt)"
    timeout_seconds = 600
    halts_on_failure = False

    async def execute(
        self, context: dict[str, Any], config: dict[str, Any]
    ) -> StageResult:
        site_config = context.get("site_config")
        if not _enabled(site_config):
            return StageResult(
                ok=True, detail="vision_alt_enabled=false", metrics={"skipped": True}
            )

        topic = context.get("topic", "")
        content = context.get("content", "") or ""
        pool = getattr(site_config, "_pool", None) if site_config is not None else None
        budget = (
            site_config.get_int("alt_text_budget", 120)
            if site_config is not None
            else 120
        )
        task_id = context.get("task_id")
        updates: dict[str, Any] = {}
        n = 0

        async def _recaption_tag(tag: str) -> str:
            nonlocal n
            src_m = _IMG_SRC_RE.search(tag)
            if not src_m:
                return tag
            new_alt = await caption_image(
                image_url=src_m.group(1),
                topic=topic,
                budget=budget,
                site_config=site_config,
                pool=pool,
                task_id=task_id,
            )
            if not new_alt:
                return tag  # fail-soft: keep prior alt
            safe = _clean_for_attr(new_alt)
            if not _IMG_ALT_RE.search(tag):
                return tag  # no alt attr to replace (rare; all our imgs have one)
            n += 1
            return _IMG_ALT_RE.sub(
                lambda a: f"{a.group(1)}{safe}{a.group(3)}", tag, count=1
            )

        # re.sub has no async form — walk matches manually.
        out: list[str] = []
        last = 0
        for m in _IMG_TAG_RE.finditer(content):
            out.append(content[last : m.start()])
            out.append(await _recaption_tag(m.group(0)))
            last = m.end()
        out.append(content[last:])
        new_content = "".join(out)

        updates["content"] = new_content
        if new_content != content:
            db = context.get("database_service")
            if db is not None and task_id:
                await db.update_task(task_id=task_id, updates={"content": new_content})

        # Featured image.
        feat_url = context.get("featured_image_url")
        if feat_url:
            feat_alt = await caption_image(
                image_url=feat_url,
                topic=topic,
                budget=budget,
                site_config=site_config,
                pool=pool,
                task_id=task_id,
            )
            if feat_alt:
                updates["featured_image_alt"] = _clean_for_attr(feat_alt)

        return StageResult(
            ok=True,
            detail=f"{n} inline captioned",
            context_updates=updates,
            metrics={"inline_captioned": n},
        )
