"""content.plan_image_markers — image placeholder planning + VRAM guard.

1. Numbers the writer's own markers (``[IMAGE:]`` illustrations under the
   ``writer_max_inline_images`` cap; ``[SCREENSHOT:]`` / ``[CHART:]`` evidence
   markers under their own ``writer_max_evidence_per_kind`` cap).
2. TOPS UP: if fewer illustrations were placed than the cap allows, the Image
   Decision Agent plans the remaining slots (never a second image in a section
   that already has one) and the merged body is renumbered in document order.
3. Unloads the writer LLM from VRAM before image-gen loads.

Top-up, not all-or-nothing: the atom used to run the decision agent ONLY when
the draft carried no markers at all. When the two_pass writer prompt gained
``[SCREENSHOT:]`` (2026-09-02, stack#3556) a single screenshot marker made
"markers present" true, so the agent never ran and every canonical_blog draft
that took the offer shipped with one dashboard capture and zero generated
illustrations — including four posts that had nothing to do with this system.

Produces: image_plans (list of {num, desc, screenshot_target?, chart_target?}),
updated content (with injected markers).

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
        "Number the writer's [IMAGE:]/[SCREENSHOT:]/[CHART:] markers, top up "
        "the remaining illustration slots via the Image Decision Agent, then "
        "VRAM-guard (unload writer LLM). Parses every marker into image_plans."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body (may or may not have [IMAGE-N] markers)"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="category", type="str", description="content category for image agent", required=False),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="draft with [IMAGE-N] markers injected"),
        FieldSpec(
            name="image_plans", type="list",
            description=(
                "[{num, desc, screenshot_target?, chart_target?}, ...] — one entry per placeholder. "
                "screenshot_target is present only for [SCREENSHOT: key] markers, "
                "chart_target only for [CHART: key] markers "
                "and routes that slot to the ScreenshotProvider."
            ),
        ),
    ),
    requires=("content",),
    produces=("content", "image_plans", "featured_image_subject"),
    capability_tier=None,
    cost_class="api",
    idempotent=False,
    side_effects=("llm_call",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


def _int_setting(site_config: Any, key: str, default: int) -> int:
    """Read an integer cap, tolerating a missing / stub site_config.

    The budgets feed arithmetic (``remaining = cap - placed``), so a test
    double that hands back a non-int must fall to the documented default
    rather than poison the comparison.
    """
    if site_config is None:
        return default
    try:
        val = site_config.get_int(key, default)
    except Exception:  # noqa: BLE001 — stub / unparsable → documented default
        return default
    # A MagicMock answers int() with 1 — insist on a real int, not a coercible.
    return val if isinstance(val, int) and not isinstance(val, bool) else default


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Number writer markers, top up illustrations, then VRAM-guard."""
    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {}

    topic = state.get("topic", "")
    category = state.get("category", "technology")
    site_config = state.get("site_config")

    # Writer-placed markers (blog-generation / two-pass SKILL.md): extract the
    # hero, number the inline markers, enforce the two budgets. The decision
    # agent then tops up whatever illustration slots are left (also the
    # ImageRebuildService path, which strips <img> and re-plans marker-free
    # text — there every slot is left).
    from modules.content.atoms._writer_markers import (
        extract_hero_subject,
        is_evidence_desc,
        number_inline_markers,
        renumber_placeholders,
    )
    max_inline = _int_setting(site_config, "writer_max_inline_images", 3)
    max_evidence = _int_setting(site_config, "writer_max_evidence_per_kind", 1)
    content_text, hero_subject = extract_hero_subject(content_text)
    content_text = number_inline_markers(
        content_text, max_inline, max_evidence_per_kind=max_evidence,
    )

    # Count what the writer placed. Only ILLUSTRATIONS spend the inline
    # budget — a screenshot or chart is evidence, filled by its own provider,
    # and must not read as "the illustrations are planned".
    placeholders = _PLACEHOLDER_RE.findall(content_text)
    n_writer_markers = len(placeholders)
    n_illustrations = sum(1 for _num, desc in placeholders if not is_evidence_desc(desc))
    remaining = max_inline - n_illustrations
    stages = state.get("stages") or {}
    result_extra: dict[str, Any] = {}

    if remaining > 0:
        # Ask the Image Decision Agent to plan + inject the REMAINING slots,
        # numbered after the writer's markers. This runs BEFORE the VRAM
        # guard below: the guard unloads the local LLM to make room for
        # image-gen, but the decision agent IS a local-LLM call — with both
        # pinned to the same 31B model, the old order forced a full 17 GB
        # reload right before the call, which under ComfyUI/image-gen VRAM
        # contention blew the 300 s provider timeout on every run for a week.
        from modules.content.atoms._image_helpers import plan_and_inject_placeholders
        content_text, plan = await plan_and_inject_placeholders(
            content_text, topic, category, site_config=site_config,
            max_images=remaining, start_num=n_writer_markers + 1,
        )
        if plan is not None and plan.get("agent_error"):
            stages["2c_image_agent_error"] = plan["agent_error"]
            # Loud, not silent: zero generated illustrations because the
            # planner FAILED is a pipeline defect the operator must see
            # (Findings board / Discord), not an editorial choice. Every
            # canonical_blog post 2026-08-17→23 shipped image-less this way,
            # unflagged.
            kept = (
                f"only its {n_writer_markers} writer-placed marker(s)"
                if n_writer_markers else "no inline images"
            )
            try:
                from utils.findings import emit_finding
                _tid = str(state.get("task_id") or "")
                emit_finding(
                    source="content.plan_image_markers",
                    kind="inline_images_skipped",
                    title="Inline images skipped — image decision agent failed",
                    body=(
                        f"Task {_tid[:8]}: the Image Decision Agent returned no "
                        f"plan, so this draft ships with {kept} and no generated "
                        f"illustrations. Reason: {plan['agent_error']}"
                    ),
                    severity="warn",
                    dedup_key=f"inline_images_skipped:{_tid}",
                    extra={
                        "task_id": _tid or None,
                        "error": plan["agent_error"],
                        "writer_markers": n_writer_markers,
                    },
                )
            except Exception as _fexc:  # noqa: BLE001 — telemetry never blocks the graph
                logger.warning("[content.plan_image_markers] finding emit failed: %s", _fexc)
        if plan is not None and plan.get("featured_image_plan"):
            # Surface featured image plan as a side-output for downstream.
            # We return it here so the state seam preserves it.
            result_extra = {"featured_image_plan": plan["featured_image_plan"]}
        # The agent's slots were numbered AFTER the writer's but sit wherever
        # their sections fall, so renumber in document order and rebuild the
        # plan list from the final body — the two can never disagree.
        content_text = renumber_placeholders(content_text)
        placeholders = _PLACEHOLDER_RE.findall(content_text)
    else:
        logger.info(
            "[content.plan_image_markers] writer placed %d illustration(s) "
            "(cap %d) — decision agent not needed",
            n_illustrations, max_inline,
        )

    # VRAM guard: unload the local writer/planner LLM before image-gen (the
    # next node) may load. Deliberately AFTER the decision agent — see above.
    try:
        from services.llm_providers.ollama_unload import maybe_unload_writer_before_image_gen
        await maybe_unload_writer_before_image_gen(
            site_config=site_config,
            stage_label="content.plan_image_markers",
        )
    except Exception as exc:
        # silent-ok: the unload is an OPTIMISATION, not the protection. If it
        # is skipped the writer model stays resident and image-gen loads
        # alongside it; the SDXL VRAM gate downstream is what actually
        # refuses to over-subscribe, and it fails loudly on its own.
        logger.debug("[content.plan_image_markers] VRAM guard skipped: %s", exc)

    # Split the `screenshot:` / `chart:` prefixes back out of the description
    # (see _writer_markers). Plans carrying a screenshot_target are routed to
    # the ScreenshotProvider by content.generate_images, and a chart_target to
    # the ChartProvider — both instead of image-gen, because diffusion renders
    # axis labels and dashboards as garbled glyphs.
    from modules.content.atoms._writer_markers import (
        split_chart_target,
        split_screenshot_target,
    )

    image_plans = []
    for num, desc in placeholders:
        plan_desc, screenshot_target = split_screenshot_target(desc)
        chart_target = None
        if not screenshot_target:
            plan_desc, chart_target = split_chart_target(plan_desc)
        # Named `entry`, not `plan` — `plan` is already bound above by
        # plan_and_inject_placeholders and mypy (correctly) rejects the shadow.
        entry: dict[str, Any] = {"num": num, "desc": plan_desc}
        if screenshot_target:
            entry["screenshot_target"] = screenshot_target
        elif chart_target:
            entry["chart_target"] = chart_target
        image_plans.append(entry)

    result: dict[str, Any] = {
        "content": content_text,
        "image_plans": image_plans,
    }
    if hero_subject:
        result["featured_image_subject"] = hero_subject
    if result_extra:
        result.update(result_extra)
    if stages:
        result["stages"] = stages
    return result


__all__ = ["ATOM_META", "run"]
