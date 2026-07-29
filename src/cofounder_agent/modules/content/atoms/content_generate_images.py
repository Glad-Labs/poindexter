"""content.generate_images — generate images for each planned placeholder.

Takes image_plans produced by content.plan_image_markers and generates one
image per plan: image-gen primary, Pexels fallback. Records media_assets rows.

Produces: image_results (list of {num, url, alt_text, source}).

Image-gen generation is batched across ALL plans in one call (one Ollama lock
+ one image-gen lock total, not per-plan) — poindexter#733 / poindexter#841.
Pexels fallback stays per-image since it's a single HTTP call, not GPU-locked.

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

    from modules.content.atoms._image_helpers import (
        batch_generate_inline_image_urls,
        record_inline_image_asset,
        try_pexels,
    )
    from services.image_service import get_image_service

    topic = state.get("topic", "")
    task_id = state.get("task_id")
    post_id = state.get("post_id")
    site_config = state.get("site_config")
    platform = state.get("platform")
    image_service = state.get("image_service") or get_image_service(site_config=site_config)  # type: ignore[arg-type]

    # poindexter#733 / poindexter#841 — one Ollama lock + one image-gen lock
    # for ALL plans in this call, instead of 2N per-plan lock acquisitions.
    placeholders = [
        (str(plan.get("num", "")), (plan.get("desc") or "").strip())
        for plan in image_plans
    ]
    image_gen_urls = await batch_generate_inline_image_urls(
        placeholders,
        site_config=site_config, task_id=task_id, platform=platform,
    )

    used_image_ids: set[str] = set()
    image_results: list[dict[str, Any]] = []

    for plan, image_gen_url in zip(image_plans, image_gen_urls, strict=True):
        num = str(plan.get("num", ""))
        desc = (plan.get("desc") or "").strip()
        search_query = desc if desc else topic
        alt_text = _build_alt_text(desc, topic, site_config)

        img_url: str | None = None
        source = "none"

        # Strategy 1: the batched image-gen result for this plan.
        if image_gen_url and image_gen_url not in used_image_ids:
            used_image_ids.add(image_gen_url)
            img_url = image_gen_url
            source = "image_gen"
            await record_inline_image_asset(
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

        if img_url is None and _stock_fallback_enabled(site_config):
            # Strategy 2: Pexels — only when the operator has opted in.
            pexels = await try_pexels(search_query, topic, image_service)
            if pexels is not None:
                pexels_url, photographer = pexels
                if pexels_url not in used_image_ids:
                    used_image_ids.add(pexels_url)
                    img_url = pexels_url
                    source = "pexels"
                    alt_text = f"Photo by {photographer}"
                    await record_inline_image_asset(
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

        if source != "image_gen":
            _emit_downgrade_finding(
                num=num, source=source, task_id=task_id,
                topic=topic, search_query=search_query,
            )

        image_results.append({
            "num": num,
            "url": img_url,
            "alt_text": alt_text,
            "source": source,
        })

    return {"image_results": image_results}


def _stock_fallback_enabled(site_config: Any) -> bool:
    """Whether a failed image-gen render may fall back to stock photography.

    Default OFF. Owned imagery is the brand asset — a stock photo dropped in
    silently is a downgrade the pipeline never disclosed, and it ran that way
    for weeks unnoticed. Kept as a setting rather than deleted so a fork that
    wants stock can just flip it on.

    Note this gates the FALLBACK only. Stock chosen deliberately — the video
    director picking Pexels for a shot that needs real photography — is a
    different path and is unaffected.
    """
    if site_config is None:
        return False
    return site_config.get_bool("image_stock_fallback_enabled", False)


def _emit_downgrade_finding(
    *, num: str, source: str, task_id: Any, topic: str, search_query: str,
) -> None:
    """Surface an inline image that did not come from image-gen.

    The whole reason this went unnoticed for weeks: the fallback logged at
    warning level per-image and then reported success, so nothing aggregated
    and nothing paged. Per the QA-rail convention, a degraded path announces
    itself rather than passing as a clean run.
    """
    from utils.findings import emit_finding

    if source == "pexels":
        title = "Inline image fell back to stock — image-gen failed"
        body = (
            f"Inline image {num} on task {task_id} was rendered from Pexels "
            f"because image-gen produced nothing for {search_query!r}. The post "
            "ships with stock art where owned art was intended."
        )
    else:
        title = "Inline image missing — image-gen failed, stock fallback off"
        body = (
            f"Inline image {num} on task {task_id} has no image: image-gen "
            f"produced nothing for {search_query!r} and image_stock_fallback_"
            "enabled is false, so no stock substitute was used."
        )
    emit_finding(
        source="content.generate_images",
        kind="image_gen_downgrade",
        title=title,
        body=f"{body}\n\nTopic: {topic}",
        severity="warn",
        dedup_key=f"image-gen-downgrade:{task_id}:{num}",
        extra={"task_id": str(task_id or ""), "placeholder_num": num, "source": source},
    )


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
