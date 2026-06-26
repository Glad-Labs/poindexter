"""content.generate_images — generate images for each planned placeholder.

Takes image_plans produced by content.plan_image_markers and generates one
image per plan: image-gen primary, Pexels fallback. Records media_assets rows.

Produces: image_results (list of {num, url, alt_text, source}).

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.generate_images",
    type="atom",
    version="1.0.0",
    description=(
        "Generate one image per image_plan entry: image-gen primary, Pexels fallback. "
        "Records media_assets rows. Returns image_results list."
    ),
    inputs=(
        FieldSpec(name="image_plans", type="list", description="[{num, desc}, ...]"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
        FieldSpec(name="image_service", type="object", description="image service instance", required=False),
        FieldSpec(name="platform", type="object", description="capability handle", required=False),
    ),
    outputs=(
        FieldSpec(name="image_results", type="list", description="[{num, url, alt_text, source}, ...]"),
    ),
    requires=("image_plans",),
    produces=("image_results",),
    capability_tier=None,
    cost_class="compute",
    idempotent=False,
    side_effects=("gpu_call", "r2_upload", "db_write"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Generate images for each plan entry."""
    image_plans = state.get("image_plans") or []
    if not image_plans:
        return {"image_results": []}

    from modules.content.stages.replace_inline_images import (
        _record_inline_image_asset,
        _try_pexels,
        _try_image_gen,
    )
    from services.image_service import get_image_service

    topic = state.get("topic", "")
    task_id = state.get("task_id")
    post_id = state.get("post_id")
    site_config = state.get("site_config")
    platform = state.get("platform")
    image_service = state.get("image_service") or get_image_service(site_config=site_config)  # type: ignore[arg-type]

    used_image_ids: set[str] = set()
    image_results: list[dict[str, Any]] = []

    for plan in image_plans:
        num = str(plan.get("num", ""))
        desc = (plan.get("desc") or "").strip()
        search_query = desc if desc else topic
        alt_text = _build_alt_text(desc, topic, site_config)

        img_url: str | None = None
        source = "none"

        # Strategy 1: image-gen.
        image_gen_url = await _try_image_gen(
            num, search_query, topic,
            site_config=site_config,
            task_id=task_id,
            platform=platform,
        )
        if image_gen_url and image_gen_url not in used_image_ids:
            used_image_ids.add(image_gen_url)
            img_url = image_gen_url
            source = "image_gen"
            await _record_inline_image_asset(
                site_config=site_config,
                post_id=post_id,
                public_url=image_gen_url,
                provider_plugin="image.image_gen",
                # R2UploadService converts PNG→WebP at upload time (#732).
                width=1024, height=1024, mime_type="image/webp",
                metadata={
                    "placeholder_num": num,
                    "alt_text": alt_text,
                    "task_id": str(task_id or ""),
                    "search_query": search_query,
                },
            )

        if img_url is None:
            # Strategy 2: Pexels.
            pexels = await _try_pexels(search_query, topic, image_service)
            if pexels is not None:
                pexels_url, photographer = pexels
                if pexels_url not in used_image_ids:
                    used_image_ids.add(pexels_url)
                    img_url = pexels_url
                    source = "pexels"
                    alt_text = f"Photo by {photographer}"
                    await _record_inline_image_asset(
                        site_config=site_config,
                        post_id=post_id,
                        public_url=pexels_url,
                        provider_plugin="image.pexels",
                        width=650, height=433, mime_type="image/jpeg",
                        metadata={
                            "placeholder_num": num,
                            "alt_text": alt_text,
                            "task_id": str(task_id or ""),
                            "photographer": photographer,
                        },
                    )

        image_results.append({
            "num": num,
            "url": img_url,
            "alt_text": alt_text,
            "source": source,
        })

    return {"image_results": image_results}


def _build_alt_text(desc: str, topic: str, site_config: Any) -> str:
    """Build alt text from desc or topic."""
    import re

    from services.alt_text import sanitize_alt_text
    alt = desc if desc else f"{topic} illustration"
    alt = alt.replace("[", "").replace("]", "").replace("\n", " ")
    alt = re.sub(r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", alt).strip()
    budget = (
        site_config.get_int("alt_text_budget", 120)
        if site_config is not None else 120
    )
    return sanitize_alt_text(alt, budget=budget, topic=topic)


__all__ = ["ATOM_META", "run"]
