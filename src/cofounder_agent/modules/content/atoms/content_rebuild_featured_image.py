"""content.rebuild_featured_image — regenerate the featured/hero image on a rebuild.

Faithful port of ``ImageRebuildService._gen_featured`` (the synchronous
image-rebuild path this graph replaces): the same two-strategy as inline
slots — image-gen primary, Pexels fallback — via the sanctioned
``modules.content.atoms._image_helpers`` seam. ``try_image_gen`` returns an
already-uploaded R2 URL, so no separate upload step is needed.

The prompt prefers the Image Decision Agent's ``featured_image_plan`` (a
side-output of ``content.plan_image_markers`` upstream in this graph), then
the writer's ``featured_image_subject``, then the topic.

Never raises on an empty result — it reports ``featured_source='none'`` and
leaves the verdict to ``content.image_rebuild_gate`` so the operator gets ONE
aggregate fail-loud message covering every slot (inline + featured).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.rebuild_featured_image",
    type="atom",
    version="1.0.0",
    description=(
        "Regenerate the featured image (image-gen primary, Pexels fallback) "
        "for the image_rebuild graph; the gate atom judges the result."
    ),
    inputs=(
        FieldSpec(name="topic", type="str", description="article topic (prompt fallback)"),
        FieldSpec(name="featured_image_plan", type="dict", description="Image Decision Agent hero plan", required=False),
        FieldSpec(name="featured_image_subject", type="str", description="writer's [HERO-IMAGE:] subject", required=False),
        FieldSpec(name="task_id", type="str", description="rebuild task id (provenance)", required=False),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
        FieldSpec(name="image_service", type="object", description="image service instance", required=False),
        FieldSpec(name="platform", type="object", description="capability handle", required=False),
    ),
    outputs=(
        FieldSpec(name="featured_image_url", type="str", description="R2/Pexels URL ('' when nothing produced)"),
        FieldSpec(name="featured_source", type="str", description="image_gen | pexels | none"),
    ),
    requires=("topic",),
    produces=("featured_image_url", "featured_source"),
    capability_tier=None,
    cost_class="compute",
    idempotent=False,
    side_effects=("gpu_call", "r2_upload"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from modules.content.atoms._image_helpers import try_image_gen, try_pexels

    topic = state.get("topic", "") or ""
    plan = state.get("featured_image_plan")
    desc = (
        (plan.get("prompt") if isinstance(plan, dict) else "")
        or state.get("featured_image_subject")
        or topic
    )
    site_config = state.get("site_config")
    platform = state.get("platform")
    task_id = state.get("task_id")

    url = await try_image_gen(
        "featured", desc,
        site_config=site_config, task_id=task_id, platform=platform,
    )
    if url:
        logger.info("[content.rebuild_featured_image] image_gen hero: %s", url[:80])
        return {"featured_image_url": url, "featured_source": "image_gen"}

    image_service = state.get("image_service")
    if image_service is None:
        try:
            from services.image_service import get_image_service
            image_service = get_image_service(site_config=site_config)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning(
                "[content.rebuild_featured_image] image service unavailable for "
                "Pexels fallback: %s", exc,
            )
            image_service = None
    if image_service is not None:
        pex = await try_pexels(desc, topic, image_service)
        if pex:
            logger.info("[content.rebuild_featured_image] Pexels hero: %s", pex[0][:80])
            return {"featured_image_url": pex[0], "featured_source": "pexels"}

    logger.warning("[content.rebuild_featured_image] no featured image produced (desc=%r)", desc[:80])
    return {"featured_image_url": "", "featured_source": "none"}


__all__ = ["ATOM_META", "run"]
