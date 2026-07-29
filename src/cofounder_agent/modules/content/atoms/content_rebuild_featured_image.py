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
    from modules.content.atoms._image_helpers import (
        stock_fallback_enabled,
        try_image_gen,
        try_pexels,
    )

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

    # Stock fallback is opt-in (2026-07-28): the global
    # `image_stock_fallback_enabled` default-OFF, overridden per run by the
    # operator's explicit `rebuild-images --allow-stock`. Without the per-run
    # override that flag would relax the rebuild GATE while this atom quietly
    # refused to produce stock at all.
    allow_stock = bool(state.get("allow_stock", False))
    if not stock_fallback_enabled(site_config, allow_stock=allow_stock):
        logger.warning(
            "[content.rebuild_featured_image] image-gen produced nothing and "
            "stock fallback is disabled (desc=%r)", desc[:80],
        )
        _emit_rebuild_downgrade_finding(
            source="none", task_id=task_id, topic=topic, allow_stock=allow_stock,
        )
        return {"featured_image_url": "", "featured_source": "none"}

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
            _emit_rebuild_downgrade_finding(
                source="pexels", task_id=task_id, topic=topic, allow_stock=allow_stock,
            )
            return {"featured_image_url": pex[0], "featured_source": "pexels"}

    logger.warning("[content.rebuild_featured_image] no featured image produced (desc=%r)", desc[:80])
    _emit_rebuild_downgrade_finding(
        source="none", task_id=task_id, topic=topic, allow_stock=allow_stock,
    )
    return {"featured_image_url": "", "featured_source": "none"}


def _emit_rebuild_downgrade_finding(
    *, source: str, task_id: Any, topic: str, allow_stock: bool,
) -> None:
    """Surface a rebuilt hero that did not come from image-gen.

    Counterpart to the findings on the content-pipeline paths. The rebuild
    gate atom already fails the RUN loud on stock, but only when the operator
    did not pass --allow-stock; with the flag set the downgrade would
    otherwise pass entirely unrecorded.
    """
    from utils.findings import emit_finding

    if source == "pexels":
        title = "Rebuilt hero fell back to stock — image-gen failed"
        body = (
            f"The image_rebuild run for task {task_id} produced a stock hero "
            "because image-gen returned nothing (operator passed --allow-stock)."
        )
    else:
        title = "Rebuilt hero missing — image-gen produced nothing"
        body = (
            f"The image_rebuild run for task {task_id} produced no hero: "
            "image-gen returned nothing and stock fallback was "
            f"{'permitted but also empty' if allow_stock else 'disabled'}."
        )
    emit_finding(
        source="content.rebuild_featured_image",
        kind="image_gen_downgrade",
        title=title,
        body=f"{body}\n\nTopic: {topic}",
        severity="warn",
        dedup_key=f"rebuild-hero-downgrade:{task_id}",
        extra={"task_id": str(task_id or ""), "source": source,
               "allow_stock": allow_stock},
    )


__all__ = ["ATOM_META", "run"]
