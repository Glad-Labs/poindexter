"""content.plan_image_markers — VRAM guard + image placeholder planning.

First unloads the writer LLM from VRAM (deterministic guard), then:
- If [IMAGE-N] markers already exist in content: parse and surface them.
- If no markers: calls the Image Decision Agent LLM to plan + inject them.

Produces: image_plans (list of {num, desc}), updated content (with injected markers).

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]")

ATOM_META = AtomMeta(
    name="content.plan_image_markers",
    type="atom",
    version="1.0.0",
    description=(
        "VRAM guard (unload writer LLM) then inject [IMAGE-N] markers via the "
        "Image Decision Agent when the draft has none. Parses existing markers "
        "into image_plans."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body (may or may not have [IMAGE-N] markers)"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="category", type="str", description="content category for image agent", required=False),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="draft with [IMAGE-N] markers injected"),
        FieldSpec(name="image_plans", type="list", description="[{num, desc}, ...] — one entry per placeholder"),
    ),
    requires=("content",),
    produces=("content", "image_plans"),
    capability_tier=None,
    cost_class="api",
    idempotent=False,
    side_effects=("llm_call",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """VRAM guard + placeholder planning."""
    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {}

    topic = state.get("topic", "")
    category = state.get("category", "technology")
    site_config = state.get("site_config")

    # VRAM guard: unload writer LLM before image-gen may load.
    try:
        from services.llm_providers.ollama_unload import maybe_unload_writer_before_image_gen
        await maybe_unload_writer_before_image_gen(
            site_config=site_config,
            stage_label="content.plan_image_markers",
        )
    except Exception as exc:
        logger.debug("[content.plan_image_markers] VRAM guard skipped: %s", exc)

    # Check for existing markers.
    placeholders = _PLACEHOLDER_RE.findall(content_text)
    stages = state.get("stages") or {}

    if not placeholders:
        # Ask the Image Decision Agent to plan + inject.
        from modules.content.stages.replace_inline_images import _plan_and_inject_placeholders
        content_text, plan = await _plan_and_inject_placeholders(
            content_text, topic, category, site_config=site_config,
        )
        if plan is not None and plan.get("agent_error"):
            stages["2c_image_agent_error"] = plan["agent_error"]
        if plan is not None and plan.get("featured_image_plan"):
            # Surface featured image plan as a side-output for downstream.
            # We return it here so the state seam preserves it.
            result_extra = {"featured_image_plan": plan["featured_image_plan"]}
        else:
            result_extra: dict[str, Any] = {}  # type: ignore[no-redef]
        placeholders = _PLACEHOLDER_RE.findall(content_text)
    else:
        result_extra = {}

    image_plans = [
        {"num": num, "desc": desc.strip()} for num, desc in placeholders
    ]

    result: dict[str, Any] = {
        "content": content_text,
        "image_plans": image_plans,
    }
    if result_extra:
        result.update(result_extra)
    if stages:
        result["stages"] = stages
    return result


__all__ = ["ATOM_META", "run"]
