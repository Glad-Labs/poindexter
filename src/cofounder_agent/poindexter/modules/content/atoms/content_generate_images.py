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

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.generate_images",
    type="atom",
    version="1.0.0",
    description=(
        "Generate one image per image_plan entry: image-gen primary, Pexels fallback. "
        "Plans carrying a screenshot_target bypass generation and capture an "
        "allow-listed operator surface instead. Records media_assets rows. "
        "Returns image_results list."
    ),
    inputs=(
        FieldSpec(
            name="image_plans", type="list",
            description="[{num, desc, screenshot_target?}, ...]",
        ),
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

    from poindexter.modules.content.atoms._image_helpers import (
        batch_generate_inline_image_urls,
        record_inline_image_asset,
        stock_fallback_enabled,
        try_pexels,
    )
    from poindexter.services.image_service import get_image_service

    topic = state.get("topic", "")
    task_id = state.get("task_id")
    post_id = state.get("post_id")
    site_config = state.get("site_config")
    platform = state.get("platform")
    image_service = state.get("image_service") or get_image_service(site_config=site_config)  # type: ignore[arg-type]
    # Charts are drawn from live rows, so this branch needs a pool. Resolved
    # once here rather than per-plan; None simply means no chart renders.
    from poindexter.modules.content.atoms._pool import resolve_pool

    pool = resolve_pool(state, atom="content.generate_images")
    # Per-run override from `rebuild-images --allow-stock`; absent (False) on
    # the ordinary canonical_blog path, where the global setting governs.
    allow_stock = bool(state.get("allow_stock", False))
    # The post's research — what a writer [DATA-CHART:] is verified against
    # (services.data_chart). Read straight off the state like the citation
    # atoms do; an empty string means a data chart cannot be verified and
    # renders nothing.
    research_context = str(state.get("research_context") or "")

    # Screenshot slots never reach the diffusion batch: they resolve against
    # an allow-listed URL, cost no GPU, and a diffusion "impression of a
    # dashboard" is exactly the output the marker exists to avoid.
    gen_plans = [
        p for p in image_plans
        if not p.get("screenshot_target") and not p.get("chart_target")
    ]

    # poindexter#733 / poindexter#841 — one Ollama lock + one image-gen lock
    # for ALL plans in this call, instead of 2N per-plan lock acquisitions.
    placeholders = [
        (str(plan.get("num", "")), (plan.get("desc") or "").strip())
        for plan in gen_plans
    ]
    image_gen_urls = (
        await batch_generate_inline_image_urls(
            placeholders,
            site_config=site_config, task_id=task_id, platform=platform,
        )
        if placeholders
        else []
    )
    # Keyed by placeholder number because the batch list is now shorter than
    # image_plans whenever a screenshot slot is present — a positional zip
    # would silently pair plan N with plan N+1's image.
    gen_url_by_num = {
        num: url
        for (num, _desc), url in zip(placeholders, image_gen_urls, strict=True)
    }

    used_image_ids: set[str] = set()
    image_results: list[dict[str, Any]] = []

    for plan in image_plans:
        num = str(plan.get("num", ""))
        desc = (plan.get("desc") or "").strip()
        search_query = desc if desc else topic
        alt_text = _build_alt_text(desc, topic, site_config)

        screenshot_target = str(plan.get("screenshot_target") or "").strip()
        if screenshot_target:
            shot = await _capture_screenshot(
                screenshot_target,
                site_config=site_config, task_id=task_id, post_id=post_id,
                num=num,
            )
            image_results.append(shot)
            continue

        chart_target = str(plan.get("chart_target") or "").strip()
        if chart_target:
            chart = await _render_chart(
                chart_target,
                site_config=site_config, task_id=task_id, post_id=post_id,
                num=num, pool=pool, research_context=research_context,
            )
            image_results.append(chart)
            continue

        image_gen_url = gen_url_by_num.get(num)
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

        if img_url is None and stock_fallback_enabled(
            site_config, allow_stock=allow_stock,
        ):
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


def _emit_downgrade_finding(
    *, num: str, source: str, task_id: Any, topic: str, search_query: str,
) -> None:
    """Surface an inline image that did not come from image-gen.

    The whole reason this went unnoticed for weeks: the fallback logged at
    warning level per-image and then reported success, so nothing aggregated
    and nothing paged. Per the QA-rail convention, a degraded path announces
    itself rather than passing as a clean run.
    """
    from poindexter.utils.findings import emit_finding

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


async def _capture_screenshot(
    target: str,
    *,
    site_config: Any,
    task_id: Any,
    post_id: Any,
    num: str,
) -> dict[str, Any]:
    """Resolve one ``[SCREENSHOT: target]`` slot via the screenshot provider.

    Returns an ``image_results`` entry either way. On failure the entry carries
    ``url=None`` and ``source="none"``, which ``content.inject_images`` handles
    by stripping the placeholder — the post simply ships without that image
    rather than substituting a diffusion render that would misrepresent a real
    dashboard.
    """
    from poindexter.modules.content.atoms._image_helpers import record_inline_image_asset
    from poindexter.services.image_providers.screenshot import ScreenshotProvider

    config: dict[str, Any] = {"_site_config": site_config}
    if site_config is not None:
        config["targets"] = site_config.get(
            "plugin.image_provider.screenshot.targets", "",
        )
        config["upload_to"] = site_config.get(
            "plugin.image_provider.screenshot.upload_to", "r2",
        )
        config["timeout_ms"] = site_config.get_int(
            "plugin.image_provider.screenshot.timeout_ms", 60000,
        )

    try:
        results = await ScreenshotProvider().fetch(target, config)
    except Exception as e:
        logger.warning(
            "[content.generate_images] screenshot target %r raised: %s",
            target, e,
        )
        results = []

    if not results:
        _emit_screenshot_finding(target=target, task_id=task_id, num=num)
        return {"num": num, "url": None, "alt_text": "", "source": "none"}

    shot = results[0]
    await record_inline_image_asset(
        site_config=site_config,
        post_id=post_id,
        public_url=shot.url,
        provider_plugin="image.screenshot",
        # R2UploadService converts PNG→WebP at upload time (#732).
        width=int(shot.width or 0), height=int(shot.height or 0),
        mime_type="image/webp",
        metadata={
            "placeholder_num": num,
            "alt_text": shot.alt_text,
            "task_id": str(task_id or ""),
            "screenshot_target": target,
            "captured_url": shot.metadata.get("captured_url", ""),
        },
    )
    return {
        "num": num,
        "url": shot.url,
        "alt_text": shot.alt_text,
        "source": "screenshot",
        # Carried so content.inject_images can set truthful <img> dimensions —
        # captures vary wildly in aspect ratio and the image-gen branch's
        # 1024x1024 square would be wrong for every one of them.
        "width": shot.width,
        "height": shot.height,
    }


def _emit_screenshot_finding(*, target: str, task_id: Any, num: str) -> None:
    """Page the operator when a screenshot slot could not be filled.

    Distinct from ``image_gen_downgrade``: nothing was downgraded, the slot is
    simply empty. The usual cause is a target missing from the allowlist or a
    surface that was down when the pipeline ran.
    """
    from poindexter.utils.findings import emit_finding

    emit_finding(
        source="content.generate_images",
        kind="screenshot_capture_failed",
        title=f"Screenshot target '{target}' produced no image",
        body=(
            f"Placeholder [IMAGE-{num}] asked for screenshot target "
            f"'{target}' and the provider returned nothing. Either the target "
            "is absent from plugin.image_provider.screenshot.targets, or the "
            "surface was unreachable when the pipeline ran. The placeholder "
            "was dropped and the post shipped without that image."
        ),
        severity="warn",
        dedup_key=f"screenshot-capture-failed:{task_id}:{num}",
        extra={
            "task_id": str(task_id or ""),
            "placeholder_num": num,
            "screenshot_target": target,
        },
    )


def _build_alt_text(desc: str, topic: str, site_config: Any) -> str:
    """Build alt text from desc or topic."""
    import re

    from poindexter.services.alt_text import sanitize_alt_text
    alt = desc if desc else f"{topic} illustration"
    alt = alt.replace("[", "").replace("]", "").replace("\n", " ")
    alt = re.sub(r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", alt).strip()
    budget = (
        site_config.get_int("alt_text_budget", 120)
        if site_config is not None else 120
    )
    return sanitize_alt_text(alt, budget=budget, topic=topic)


__all__ = ["ATOM_META", "run"]


async def _catalog_chart_recently_used(
    key: str, *, pool: Any, site_config: Any, post_id: Any, task_id: Any = None,
) -> bool:
    """Did catalog chart ``key`` render for ANOTHER post in the cooldown window?

    The catalog is small (one chart on 2026-10-05), so without this every post
    that asks for a chart gets the same image — ten posts in thirty days, three
    of them published within a week. ``chart_catalog_repeat_cooldown_days``
    (``0`` = off) spaces a catalog chart out; a writer ``[DATA-CHART:]`` is
    built from that post's own research and is never held back. Fails open:
    no pool or a failed lookup lets the chart render.
    """
    try:
        days = float(site_config.get("chart_catalog_repeat_cooldown_days", 7) or 0) if site_config is not None else 7.0
    except (TypeError, ValueError):
        days = 7.0
    if days <= 0 or pool is None:
        return False
    try:
        hit = await pool.fetchval(
            """
            SELECT 1 FROM media_assets
             WHERE provider_plugin = 'image.chart'
               AND metadata->>'chart_target' = $1
               AND created_at > now() - make_interval(secs => $2)
               AND post_id::text IS DISTINCT FROM $3
               AND COALESCE(metadata->>'task_id', '') IS DISTINCT FROM $4
             LIMIT 1
            """,
            key, days * 86400.0, str(post_id) if post_id else None,
            # The post row may not exist yet at generate time, so a re-run of
            # this task must not count its own earlier chart as "another post".
            str(task_id or ""),
        )
    except Exception as e:  # noqa: BLE001 — silent-ok: the cooldown is a nicety; a failed lookup must not cost the post its chart
        logger.debug("[content.generate_images] chart cooldown lookup failed: %s", e)
        return False
    return bool(hit)


async def _render_chart(
    key: str,
    *,
    site_config: Any,
    task_id: Any,
    post_id: Any,
    num: str,
    pool: Any = None,
    research_context: str = "",
) -> dict[str, Any]:
    """Resolve a chart target to real data and render it.

    Two kinds of target. A catalogued key resolves through
    ``services.chart_catalog`` to our own measurements. A ``data:`` target is
    a writer ``[DATA-CHART:]`` whose every value must be stated, beside its
    label, in ``research_context`` (``services.data_chart``); one unverified
    point drops the chart.

    The key → data step happens HERE, in a service call, never inside the image
    provider: ``ChartProvider`` takes a finished data spec and has no query
    surface, because a query surface reachable from a writer-emitted marker is
    an injection seam (the ScreenshotProvider allowlist lesson).

    Every failure — unknown key, key disabled here, too little data, a render
    that returned nothing — resolves to the same empty slot the screenshot
    branch produces. A wrong chart is worse than no chart.
    """
    from poindexter.modules.content.atoms._image_helpers import record_inline_image_asset
    from poindexter.services.chart_catalog import resolve as resolve_chart
    from poindexter.services.data_chart import DATA_TARGET_PREFIX, build_verified_spec

    if key.startswith(DATA_TARGET_PREFIX):
        spec, reasons = build_verified_spec(
            key[len(DATA_TARGET_PREFIX):], research_context, site_config=site_config,
        )
        if spec is None:
            logger.warning(
                "[content.generate_images] data chart dropped — %s",
                "; ".join(reasons) or "unverified",
            )
            return {"num": num, "url": None, "alt_text": "", "source": "none"}
    elif await _catalog_chart_recently_used(
        key, pool=pool, site_config=site_config, post_id=post_id, task_id=task_id,
    ):
        logger.info(
            "[content.generate_images] chart %r ran on another post within "
            "chart_catalog_repeat_cooldown_days — leaving the slot empty so "
            "consecutive posts do not carry the same chart", key,
        )
        return {"num": num, "url": None, "alt_text": "", "source": "none"}
    else:
        spec = await resolve_chart(key, pool=pool, site_config=site_config)
    if spec is None:
        logger.warning(
            "[content.generate_images] chart key %r did not resolve — "
            "leaving the slot empty", key,
        )
        return {"num": num, "url": None, "alt_text": "", "source": "none"}

    import json

    from poindexter.plugins.registry import get_image_providers

    provider = next(
        (p for p in get_image_providers() if getattr(p, "name", "") == "chart"),
        None,
    )
    if provider is None:  # pragma: no cover - registry always carries it
        logger.warning("[content.generate_images] chart provider not registered")
        return {"num": num, "url": None, "alt_text": "", "source": "none"}

    payload = json.dumps({
        "form": spec.form,
        "title": spec.title,
        "subtitle": spec.subtitle,
        "categories": spec.categories,
        "series": [{"label": s.label, "values": s.values} for s in spec.series],
        "value_label": spec.value_label,
        "value_suffix": spec.value_suffix,
        "source": spec.source,
        "width": spec.width,
    })
    try:
        results = await provider.fetch(payload, {"_site_config": site_config})
    except Exception as e:  # noqa: BLE001 — a chart must never break the post
        logger.warning(
            "[content.generate_images] chart %r raised: %s", key, e,
        )
        results = []

    if not results:
        logger.warning(
            "[content.generate_images] chart %r rendered nothing", key,
        )
        return {"num": num, "url": None, "alt_text": "", "source": "none"}

    chart = results[0]
    await record_inline_image_asset(
        site_config=site_config,
        post_id=post_id,
        public_url=chart.url,
        provider_plugin="image.chart",
        # R2UploadService converts PNG→WebP at upload time (#732).
        width=int(chart.width or 0), height=int(chart.height or 0),
        mime_type="image/webp",
        metadata={
            "placeholder_num": num,
            "alt_text": chart.alt_text,
            "task_id": str(task_id or ""),
            "chart_target": key,
            "chart_source": chart.metadata.get("chart_source", ""),
        },
    )
    return {
        "num": num,
        "url": chart.url,
        # The alt text carries the full data matrix (chart_alt_text) — a PNG
        # has no table view, so this is where the numbers live for a screen
        # reader and for any later pass over the post.
        "alt_text": chart.alt_text,
        "source": "chart",
        "width": chart.width,
        "height": chart.height,
    }
