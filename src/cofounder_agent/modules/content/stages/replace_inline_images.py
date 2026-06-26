"""ReplaceInlineImagesStage — stage 2C of the content pipeline.

Finds / injects [IMAGE-N] placeholders in the draft and replaces each
with either an image-gen-generated image or a Pexels photo. Post-processes
the draft to strip any leaked image-gen prompt text.

Fully ported from ``_stage_replace_inline_images`` in content_router_service.py.
No more thin-wrapper delegation. Helper logic is split into internal
methods + module-level functions for readability; legacy behavior is
preserved byte-for-byte.

## Strategy (per placeholder)

1. **image-gen (primary)** — Ollama generates a prompt (random inline style),
   then the image-gen server renders. Path traversal guard on the returned
   path; R2 upload with local-path fallback if R2 unavailable.
2. **Pexels (fallback)** — used when image-gen fails, the server returns
   non-200, or the generated image collides with another placeholder.
3. **Remove placeholder** — if both fail, strip the placeholder so no
   raw `[IMAGE-N]` reaches the reader.

After all placeholders resolve, a small regex pass cleans up leaked
italic scene descriptions, stray photo-attribution lines, and the
like — artifacts LLMs sometimes emit adjacent to image placeholders.

## Context reads

- ``task_id`` (str), ``topic`` (str), ``content`` (str)
- ``database_service`` (must expose ``update_task``)
- ``image_service`` (falls back to ``get_image_service()``)
- ``category`` (str, default ``"technology"``) — for the image decision agent

## Context writes

- ``content`` (possibly modified)
- ``inline_images_replaced`` (int)
- ``stages["2c_inline_images_replaced"]`` (bool)
- ``stages["2c_image_agent_error"]`` (str, only if the decision agent crashed)
- ``featured_image_plan`` (optional — set when the decision agent suggests one)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
import uuid
from contextlib import suppress
from typing import Any

import httpx

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


_PLACEHOLDER_RE = re.compile(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]")
_HEADING_RE = re.compile(r"^#{2,4}\s+(.+)$", re.MULTILINE)

# 2026-05-27: bold-text pseudo-headings (``**Section Title**`` as a
# standalone line) are the writer's default structural pattern despite
# the prompt asking for real H2 markdown. Match them as a fallback so
# downstream image placement still finds anchor points. Bounded to
# 80 chars + entire-line match so a mid-paragraph ``**word**`` isn't
# mistaken for a section heading.
_BOLD_HEADING_RE = re.compile(r"^\*\*(.{1,80}?)\*\*\s*$", re.MULTILINE)


# Stylized-only fallback pool for inline illustrations. The photoreal styles
# ("photorealistic scene", "editorial photograph", "macro photograph") were
# removed deliberately: low-step image-gen butchers photoreal detail (the "PC
# hardware slop" / mangled-hands problem) and the brand is stylized, not
# photographic. Operators tune the live pool via the ``inline_image_styles``
# app_setting (JSON array of style strings); this tuple is the fallback.
# #image-zimage-and-variety.
INLINE_STYLES: tuple[str, ...] = (
    "isometric 3D illustration, clean vector style, soft shadows",
    "flat vector illustration, bold geometric shapes, limited palette",
    "thin line art on a dark background, technical schematic feel",
    "low-poly 3D geometric render, faceted surfaces",
    "cel-shaded digital illustration, crisp clean outlines",
    "dramatic silhouette composition, single accent color",
)


IMAGE_GEN_NEGATIVE_PROMPT = (
    "text, words, letters, watermark, face, person, hands, blurry, "
    "low quality, distorted, ugly, deformed"
)


def _get_image_gen_negative_prompt(site_config: Any) -> str:
    """Return operator-configured negative prompt, or the safe default."""
    if site_config is None:
        return IMAGE_GEN_NEGATIVE_PROMPT
    override = (site_config.get("image_negative_prompt", "") or "").strip()
    return override if override else IMAGE_GEN_NEGATIVE_PROMPT


def _apply_base_style(prompt: str, site_config: Any) -> str:
    """Append operator-configured base style suffix to an image-gen prompt.

    ``image_base_style_prompt`` lets operators set a niche-wide style
    (e.g. ``cyberpunk, neon accents`` for tech, ``natural light, botanical``
    for gardening) without editing per-post prompts.  Empty setting = no-op.
    """
    if site_config is None:
        return prompt
    base = (site_config.get("image_base_style_prompt", "") or "").strip()
    return f"{prompt}, {base}" if base else prompt


def _load_inline_styles(site_config: Any) -> tuple[str, ...]:
    """Inline illustration style pool — DB-configurable via the
    ``inline_image_styles`` app_setting (JSON array of style strings), with the
    stylized ``INLINE_STYLES`` tuple as the fallback. Parallels the featured
    pool's ``image_styles`` setting. #image-zimage-and-variety.
    """
    if site_config is None:
        return INLINE_STYLES
    raw = (site_config.get("inline_image_styles", "") or "").strip()
    if not raw:
        return INLINE_STYLES
    try:
        parsed = json.loads(raw)
    except Exception:
        return INLINE_STYLES
    styles = tuple(s for s in parsed if isinstance(s, str) and s.strip())
    return styles or INLINE_STYLES


def _build_inline_prompt_instruction(
    search_query: str, topic: str, style: str,
) -> str:
    """LLM instruction for an inline image-gen prompt.

    The wording lives in the ``image.inline_illustration`` skill prompt
    (UnifiedPromptManager: Langfuse override → skill YAML default), so it's
    tunable without a code edit. Falls back to a de-funnelled instruction that
    demands a concrete scene rendered in the chosen art style (replacing the
    old "describe a specific scene" line that produced literal tech slop).
    #image-zimage-and-variety.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(
            "image.inline_illustration",
            search_query=search_query, topic=topic, style=style,
        )
    except Exception:  # noqa: BLE001 — prompt resolution is best-effort
        return (
            f"Write a Stable Diffusion XL image prompt for a {style} blog "
            f"illustration depicting a concrete, specific scene about: "
            f"{search_query} (article topic: {topic}). Commit to the named art "
            "style; no people, no faces, no hands, no text. 1 sentence. "
            "Output ONLY the prompt."
        )


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class ReplaceInlineImagesStage:
    name = "replace_inline_images"
    description = "Decide + generate inline images (image-gen primary, Pexels fallback)"
    timeout_seconds = 300
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.image_service import get_image_service

        task_id = context.get("task_id")
        post_id = context.get("post_id")
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        database_service = context.get("database_service")
        category = context.get("category", "technology")
        # DI seam (glad-labs-stack#330) — content_router_service seeds
        # site_config into the stage context. Tests pass a mock SiteConfig.
        site_config = context.get("site_config")
        platform = context.get("platform")

        if not content_text:
            return StageResult(
                ok=True,
                detail="no content to process",
                metrics={"skipped": True},
            )
        if not task_id or database_service is None:
            return StageResult(
                ok=False,
                detail="context missing task_id or database_service",
            )

        # Build the image service only after the cheap guards pass —
        # ``get_image_service`` requires a real ``site_config`` (#272
        # Phase-2e), seeded into the context by content_router_service.
        image_service = context.get("image_service") or get_image_service(
            site_config=site_config  # type: ignore[arg-type]
        )

        stages = context.setdefault("stages", {})
        updates: dict[str, Any] = {}

        # VRAM guard (glad-labs-stack 2026-05-19 jank-audit finding #4):
        # the writer LLM (~20 GB for gemma3:27b) stays resident from the
        # preceding LLM stages because Ollama's default keep_alive is 5
        # min. image-gen Lightning loads ~12 GB on top, which OOMs 24 GB
        # cards and runs at ~98% VRAM on a 32 GB card. The
        # gpu_scheduler already unloads on the image-gen lock acquire, but
        # keep_alive=0 is fire-and-forget — without the grace sleep an
        # immediate /generate (the inline-image prompt build a few
        # lines below) can re-load before the writer's VRAM has been
        # released. This helper makes the unload deterministic +
        # tunable per ``app_settings.pipeline_writer_unload_before_image_gen``.
        from services.llm_providers.ollama_unload import (
            maybe_unload_writer_before_image_gen,
        )
        await maybe_unload_writer_before_image_gen(
            site_config=site_config,
            stage_label=self.name,
        )

        # Look for existing placeholders from the writer; otherwise ask
        # the Image Decision Agent to plan + inject them.
        placeholders = _PLACEHOLDER_RE.findall(content_text)
        if not placeholders:
            content_text, plan = await _plan_and_inject_placeholders(
                content_text, topic, category, site_config=site_config,
            )
            if plan is not None and plan.get("featured_image_plan"):
                updates["featured_image_plan"] = plan["featured_image_plan"]
            if plan is not None and plan.get("agent_error"):
                stages["2c_image_agent_error"] = plan["agent_error"]
            placeholders = _PLACEHOLDER_RE.findall(content_text)

        if not placeholders:
            stages["2c_inline_images_replaced"] = False
            logger.info("No [IMAGE-N] placeholders to replace")
            updates["stages"] = stages
            return StageResult(
                ok=True,
                detail="no placeholders",
                context_updates=updates,
                metrics={"inline_images_replaced": 0},
            )

        logger.info(
            "STAGE 2C: Replacing %d inline image placeholders...",
            len(placeholders),
        )

        used_image_ids: set[str] = set()
        # poindexter#733 — batch GPU work into two phases:
        #   Phase 1: one Ollama lock → generate ALL prompts
        #   Phase 2: one image-gen lock → render ALL images
        # This eliminates N×2 GPU lock acquisitions (6 for 3 images) and
        # the Ollama↔image-gen model-swap churn that caused ~95 s avg stage time.
        image_gen_urls = await _batch_generate_all_images(
            placeholders=placeholders,
            topic=topic,
            site_config=site_config,
            task_id=task_id,
            platform=platform,
        )
        for (num, desc), img_url in zip(placeholders, image_gen_urls, strict=False):
            content_text = await _resolve_one_placeholder(
                num=num,
                desc=desc,
                topic=topic,
                content_text=content_text,
                image_service=image_service,
                used_image_ids=used_image_ids,
                site_config=site_config,
                task_id=task_id,
                post_id=post_id,
                platform=platform,
                pregenerated_image_url=img_url,
            )

        content_text = _cleanup_leaked_descriptions(content_text)
        content_text = _normalize_from_router(content_text)

        # Persist the image-populated content.
        await database_service.update_task(
            task_id=task_id, updates={"content": content_text},
        )

        stages["2c_inline_images_replaced"] = True
        updates.update({
            "content": content_text,
            "inline_images_replaced": len(used_image_ids),
            "stages": stages,
        })
        logger.info("Replaced %d inline images in content", len(used_image_ids))

        return StageResult(
            ok=True,
            detail=f"{len(used_image_ids)} images replaced",
            context_updates=updates,
            metrics={"inline_images_replaced": len(used_image_ids)},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_from_router(text: str) -> str:
    """Proxy to :func:`services.text_utils.normalize_text`.

    Kept as a local helper so the call site in :meth:`ReplaceInlineImagesStage.execute`
    stays readable (``_normalize_from_router(content_text)``); lazy import
    preserves lock-free startup.
    """
    from services.text_utils import normalize_text
    return normalize_text(text)


async def _plan_and_inject_placeholders(
    content_text: str,
    topic: str,
    category: str,
    *,
    site_config: Any,
) -> tuple[str, dict[str, Any] | None]:
    """Ask the Image Decision Agent to decide + inject [IMAGE-N] placeholders.

    Returns ``(content_text, info)`` where info may carry a
    ``featured_image_plan`` (if the agent recommends one) or an
    ``agent_error`` string (if the decision agent crashed).

    ``site_config`` is the run-bound SiteConfig threaded from
    ``execute`` (``context.get("site_config")``) — ``plan_images``
    requires it post-#272 Phase-2c.
    """
    try:
        from services.image_decision_agent import plan_images
    except Exception as e:
        logger.exception("[IMAGE_AGENT] Image Decision Agent FAILED to import: %s", e)
        return content_text, {"agent_error": str(e)}

    try:
        plan = await plan_images(
            content_text, topic, category, max_images=3,
            site_config=site_config,
        )
    except Exception as agent_err:
        logger.exception("[IMAGE_AGENT] Image Decision Agent FAILED: %s", agent_err)
        return content_text, {"agent_error": str(agent_err)}

    if not plan.images:
        return content_text, None

    info: dict[str, Any] = {}
    if plan.featured_image:
        info["featured_image_plan"] = {
            "source": plan.featured_image.source,
            "style": plan.featured_image.style,
            "prompt": plan.featured_image.prompt,
        }

    # Inject placeholders at agent-selected positions.
    # 2026-05-27: include bold-text pseudo-headings (``**Title**`` on
    # its own line) when real markdown H2/H3 aren't present. Without
    # this fallback, every canonical_blog post that used bold-text
    # section dividers got zero inline images because the heading_map
    # was empty.
    real_headings = list(_HEADING_RE.finditer(content_text))
    heading_map: dict[str, re.Match[str]] = {
        re.sub(r"^#+\s*", "", h.group()).strip().lower(): h
        for h in real_headings
    }
    if not heading_map:
        bold_headings = list(_BOLD_HEADING_RE.finditer(content_text))
        heading_map = {h.group(1).strip().lower(): h for h in bold_headings}
        if heading_map:
            logger.info(
                "[IMAGE_AGENT] No real H2/H3 — anchored %d image "
                "placeholders to bold-text pseudo-headings",
                len(heading_map),
            )

    insert_positions: list[tuple[int, int, str, str]] = []
    for i, img in enumerate(plan.images):
        for heading_text, h_match in heading_map.items():
            if (
                img.section_heading.lower() in heading_text
                or heading_text in img.section_heading.lower()
            ):
                # Default: anchor at the next paragraph break after the
                # heading. Fall back to end-of-content when this is the
                # last section (no trailing ``\n\n``) — otherwise the
                # final section gets no image, which matches the prod
                # symptom: short canonical_blog posts had the writer
                # bleed the closing section to EOF, leaving image plans
                # unplaced.
                para_end = content_text.find("\n\n", h_match.end())
                if para_end < 0:
                    para_end = len(content_text)
                source_hint = f"{img.source}:{img.style}"
                insert_positions.append(
                    (para_end, i + 1, img.prompt, source_hint),
                )
                break

    # Insert in reverse so earlier positions stay valid.
    for pos, img_num, prompt, source_hint in reversed(insert_positions):
        placeholder = f"\n[IMAGE-{img_num}: {prompt} ||{source_hint}||]\n"
        content_text = content_text[:pos] + placeholder + content_text[pos:]

    n_inserted = len(_PLACEHOLDER_RE.findall(content_text))
    if n_inserted:
        logger.info(
            "[IMAGE_AGENT] Injected %d image placeholders via decision agent",
            n_inserted,
        )
    return content_text, info or None


async def _resolve_one_placeholder(
    num: str,
    desc: str,
    topic: str,
    content_text: str,
    image_service: Any,
    used_image_ids: set[str],
    *,
    site_config: Any,
    task_id: str | None,
    post_id: Any = None,
    platform: Any = None,
    pregenerated_image_url: str | None = None,
) -> str:
    """Replace one ``[IMAGE-N]`` placeholder with a real image or strip it.

    ``task_id`` is forwarded to :func:`_try_image_gen` so the GPU scheduler
    can attribute Ollama-prompt + image-gen-render electricity cost back to
    the originating pipeline task — see Glad-Labs/poindexter#157.

    ``post_id`` is forwarded so a successful image generation lands a
    ``media_assets`` row pinned to the post it belongs to (Glad-Labs/
    poindexter#161). When ``post_id`` is None (early-pipeline calls
    before the post is persisted), the row is skipped.

    ``pregenerated_image_url`` accepts an already-generated image-gen URL so
    the batched two-phase path (poindexter#733) can skip the per-placeholder
    GPU lock cycle. When set to a non-None non-empty string, Strategy 1
    uses this URL directly instead of calling ``_try_image_gen``.
    """
    from services.alt_text import sanitize_alt_text
    search_query = desc.strip() if desc else topic
    alt_text = desc.strip() if desc else f"{topic} illustration"
    # Normalize structural artefacts of the placeholder format.
    alt_text = alt_text.replace("[", "").replace("]", "").replace("\n", " ")
    alt_text = re.sub(r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", alt_text).strip()
    # GH-84: strip ``||provider:hint||`` pipeline tokens + enforce a
    # DB-configurable budget with word-boundary truncation (no mid-word chop).
    # GH-469: pass topic so image-gen-prompt-shaped descriptors fall back to
    # a topic-derived alt instead of leaking imperative-mood prompt text.
    alt_text = sanitize_alt_text(
        alt_text,
        budget=(
            site_config.get_int("alt_text_budget", 120)
            if site_config is not None else 120
        ),
        topic=topic,
    )

    # Strategy 1: image-gen.
    # Use the pre-generated URL when available (poindexter#733 batch path);
    # fall back to the per-image _try_image_gen call when not (legacy path, used
    # when callers don't go through the batched execute() loop).
    if pregenerated_image_url is not None:
        img_url = pregenerated_image_url or None
    else:
        img_url = await _try_image_gen(
            num, search_query, topic, site_config=site_config, task_id=task_id,
            platform=platform,
        )
    if img_url and img_url not in used_image_ids:
        used_image_ids.add(img_url)
        content_text = _inject_html_image(
            content_text, num, img_url, alt_text,
            width=1024, height=1024,
        )
        logger.info("  [IMAGE-%s] image-gen generated + R2 uploaded", num)
        await _record_inline_image_asset(
            site_config=site_config,
            post_id=post_id,
            public_url=img_url,
            provider_plugin="image.image_gen",
            width=1024,
            height=1024,
            # R2UploadService converts PNG→WebP at upload time (#732).
            mime_type="image/webp",
            metadata={
                "placeholder_num": num,
                "alt_text": alt_text,
                "task_id": str(task_id or ""),
                "search_query": search_query,
            },
        )
        return content_text

    # Strategy 2: Pexels.
    pexels = await _try_pexels(search_query, topic, image_service)
    if pexels is not None:
        img_url, photographer = pexels
        if img_url not in used_image_ids:
            used_image_ids.add(img_url)
            markdown_img = (
                f'\n\n<img src="{img_url}" alt="{alt_text}" '
                f'width="650" height="433" loading="lazy" />\n'
                f'<figcaption>Photo by {photographer} on Pexels</figcaption>\n\n'
            )
            content_text = re.sub(
                rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1,
            )
            logger.info("  [IMAGE-%s] Pexels image by %s", num, photographer)
            await _record_inline_image_asset(
                site_config=site_config,
                post_id=post_id,
                public_url=img_url,
                provider_plugin="image.pexels",
                width=650,
                height=433,
                mime_type="image/jpeg",
                metadata={
                    "placeholder_num": num,
                    "alt_text": alt_text,
                    "task_id": str(task_id or ""),
                    "photographer": photographer,
                },
            )
            return content_text

    # Strategy 3: strip.
    content_text = re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
    logger.warning("  [IMAGE-%s] no image source available, removed placeholder", num)
    return content_text


async def _record_inline_image_asset(
    *,
    site_config: Any,
    post_id: Any,
    public_url: str,
    provider_plugin: str,
    width: int,
    height: int,
    mime_type: str,
    metadata: dict[str, Any],
) -> None:
    """Best-effort ``media_assets`` insert for one inline image.

    Closes Glad-Labs/poindexter#161 — every inline image now lands a
    DB row so cleanup / retention / cost-attribution can find it.
    Failures log and never propagate (callers must keep going so the
    pipeline doesn't break on a DB hiccup).
    """
    if post_id is None:
        # Early pipeline runs (before the post row exists) skip the
        # insert — backfill picks them up later from the rendered HTML.
        return
    try:
        from services.media_asset_recorder import record_media_asset
    except Exception as exc:  # noqa: BLE001 — defensive import guard
        logger.debug("[STAGE2C] media_asset_recorder unavailable: %s", exc)
        return
    pool = getattr(site_config, "_pool", None)
    storage_provider = (
        "cloudflare_r2"
        if public_url.startswith("http") and "r2" in public_url
        else ("local" if public_url.startswith("/") else "external")
    )
    await record_media_asset(
        pool=pool,
        post_id=post_id,
        asset_type="inline_image",
        public_url=public_url,
        storage_path="",
        mime_type=mime_type,
        width=width,
        height=height,
        provider_plugin=provider_plugin,
        source="pipeline",
        storage_provider=storage_provider,
        metadata=metadata,
    )


async def _batch_generate_all_images(
    placeholders: list[tuple[str, str]],
    topic: str,
    *,
    site_config: Any,
    task_id: str | None,
    platform: Any,
) -> list[str | None]:
    """Generate image-gen images for all placeholders using two batched GPU locks.

    poindexter#733 — eliminates the per-placeholder Ollama↔image-gen swap churn.

    Phase 1 (one Ollama lock): generate ALL image-gen prompts sequentially.
    Phase 2 (one image-gen lock): render ALL images sequentially.

    Returns a list of URLs (or None on failure) in the same order as
    ``placeholders``. A per-placeholder failure never stops the others —
    None in the result triggers the Pexels fallback in
    ``_resolve_one_placeholder``.

    Falls back to returning a list of None values when:
    - No DB pool is available (tests / bootstrap without DB)
    - Either GPU lock acquisition fails
    """
    from services.gpu_scheduler import gpu

    n = len(placeholders)
    if n == 0:
        return []

    pool = getattr(site_config, "_pool", None) if site_config is not None else None
    if pool is None:
        logger.debug("[IMAGE-BATCH] no DB pool — skipping batched image-gen generation")
        return [None] * n

    if platform is None:
        logger.debug("[IMAGE-BATCH] no platform handle — skipping batched image-gen generation")
        return [None] * n

    image_gen_url = site_config.get("image_gen_server_url", "http://host.docker.internal:9836")
    model = site_config.get("inline_image_prompt_model", "llama3:latest")

    # ------------------------------------------------------------------ #
    # Phase 1: generate ALL prompts under a single Ollama lock            #
    # ------------------------------------------------------------------ #
    neg_prompt = _get_image_gen_negative_prompt(site_config)
    img_gen_prompts: list[str | None] = []
    try:
        async with gpu.lock(
            "ollama", model=model, task_id=task_id, phase="inline_image_prompt_batch",
        ):
            for num, desc in placeholders:
                search_query = desc.strip() if desc else topic
                inline_style = random.choice(_load_inline_styles(site_config))
                img_prompt_req = _build_inline_prompt_instruction(
                    search_query, topic, inline_style,
                )
                try:
                    result = await platform.dispatch.complete(
                        pool=pool,
                        messages=[{"role": "user", "content": img_prompt_req}],
                        model=model,
                        tier="standard",
                        timeout_s=site_config.get_int("image_prompt_timeout_seconds", 90),
                        temperature=site_config.get_float("image_prompt_temperature", 0.8),
                        max_tokens=site_config.get_int("image_prompt_max_tokens", 150),
                    )
                    img_gen_prompt = (getattr(result, "text", "") or "").strip().strip('"')
                    img_gen_prompt = _apply_base_style(img_gen_prompt, site_config)
                    if img_gen_prompt and len(img_gen_prompt) > 20:
                        logger.info(
                            "  [IMAGE-%s] image-gen prompt (batch): %s...", num, img_gen_prompt[:60],
                        )
                        img_gen_prompts.append(img_gen_prompt)
                    else:
                        logger.warning("  [IMAGE-%s] image-gen prompt too short/empty — Pexels fallback", num)
                        img_gen_prompts.append(None)
                except Exception as err:
                    logger.warning("  [IMAGE-%s] image-gen prompt generation failed: %s", num, err)
                    img_gen_prompts.append(None)
    except Exception as err:
        logger.warning("[IMAGE-BATCH] Ollama lock acquire failed: %s — falling back per-image", err)
        return [None] * n

    # ------------------------------------------------------------------ #
    # Phase 2: render ALL images under a single image-gen lock                #
    # ------------------------------------------------------------------ #
    image_gen_urls: list[str | None] = []
    render_timeout = site_config.get_int("image_render_timeout_seconds", 90) if site_config is not None else 90
    gpu_model_label = site_config.get("image_generation_model", "image_gen") if site_config is not None else "image_gen"
    try:
        async with gpu.lock(
            "image_gen", model=gpu_model_label, task_id=task_id, phase="inline_image_batch",
        ):
            async with httpx.AsyncClient(timeout=httpx.Timeout(render_timeout, connect=5.0)) as client:
                for (num, _desc), img_gen_prompt in zip(placeholders, img_gen_prompts, strict=False):
                    if img_gen_prompt is None:
                        image_gen_urls.append(None)
                        continue
                    try:
                        img_resp = await client.post(
                            f"{image_gen_url}/generate",
                            json={
                                "prompt": img_gen_prompt,
                                "negative_prompt": neg_prompt,
                                # steps / guidance omitted — server's per-model
                                # registry drives them. #image-zimage-and-variety.
                            },
                            timeout=render_timeout,
                        )
                        if img_resp.status_code != 200:
                            logger.warning(
                                "  [IMAGE-%s] image-gen returned %s (batch)", num, img_resp.status_code,
                            )
                            image_gen_urls.append(None)
                            continue
                        tmp_path = await _resolve_gen_response(img_resp, image_gen_url=image_gen_url)
                        img_url = await _upload_to_r2_with_fallback(tmp_path, site_config=site_config)
                        logger.info("  [IMAGE-%s] image-gen generated + uploaded (batch)", num)
                        image_gen_urls.append(img_url)
                    except Exception as err:
                        logger.warning("  [IMAGE-%s] image-gen render failed (batch): %s", num, err)
                        image_gen_urls.append(None)
    except Exception as err:
        logger.warning("[IMAGE-BATCH] image-gen lock acquire failed: %s — no image-gen images this run", err)
        return [None] * n

    return image_gen_urls


async def _try_image_gen(
    num: str,
    search_query: str,
    topic: str,
    *,
    site_config: Any,
    task_id: str | None,
    platform: Any = None,
) -> str | None:
    """Generate an image-gen image and return its final URL (R2 or local).

    ``task_id`` is threaded through to :meth:`gpu.lock` for both the
    Ollama prompt-build and the image-gen render so ``gpu_task_sessions`` /
    cost_logs rows attribute kWh + electricity cost to the originating
    pipeline task. Without this, the inline-image phase logged un-
    attributed sessions — see Glad-Labs/poindexter#157.
    """
    from services.gpu_scheduler import gpu

    try:
        image_gen_url = site_config.get("image_gen_server_url", "http://host.docker.internal:9836")
        model = site_config.get("inline_image_prompt_model", "llama3:latest")
        inline_style = random.choice(_load_inline_styles(site_config))
        img_prompt_req = _build_inline_prompt_instruction(
            search_query, topic, inline_style,
        )

        # Step 1: dispatcher generates the image-gen prompt. When no pool is
        # reachable (tests / bootstrap), bail to Pexels fallback by
        # returning None — the caller treats it as "no image-gen image".
        pool = getattr(site_config, "_pool", None) if site_config is not None else None
        if pool is None:
            logger.debug(
                "  [IMAGE-%s] no DB pool — skipping image-gen prompt generation", num,
            )
            return None

        async with gpu.lock(
            "ollama", model=model, task_id=task_id, phase="inline_image_prompt",
        ):
            if platform is not None:
                result = await platform.dispatch.complete(
                    pool=pool,
                    messages=[{"role": "user", "content": img_prompt_req}],
                    model=model,
                    tier="standard",
                    timeout_s=site_config.get_int("image_prompt_timeout_seconds", 90),
                    temperature=site_config.get_float("image_prompt_temperature", 0.8),
                    max_tokens=site_config.get_int("image_prompt_max_tokens", 150),
                )
            else:
                raise RuntimeError(
                    "platform handle required for dispatch — check pipeline context threading"
                )
            img_gen_prompt = (getattr(result, "text", "") or "").strip().strip('"')
        img_gen_prompt = _apply_base_style(img_gen_prompt, site_config)

        if not img_gen_prompt or len(img_gen_prompt) <= 20:
            return None

        logger.info("  [IMAGE-%s] image-gen prompt: %s...", num, img_gen_prompt[:60])

        # Step 2: image-gen renders the image
        neg_prompt = _get_image_gen_negative_prompt(site_config)
        render_timeout = site_config.get_int("image_render_timeout_seconds", 90) if site_config is not None else 90
        gpu_model_label = site_config.get("image_generation_model", "image_gen") if site_config is not None else "image_gen"
        async with gpu.lock(
            "image_gen", model=gpu_model_label,
            task_id=task_id, phase="inline_image",
        ):
            async with httpx.AsyncClient(timeout=httpx.Timeout(render_timeout, connect=5.0)) as client:
                img_resp = await client.post(
                    f"{image_gen_url}/generate",
                    json={
                        "prompt": img_gen_prompt,
                        "negative_prompt": neg_prompt,
                        # steps / guidance_scale omitted — the image-gen server's
                        # per-model registry drives them (see featured-image
                        # stage). #image-zimage-and-variety.
                    },
                    timeout=render_timeout,
                )

        if img_resp.status_code != 200:
            logger.warning("  [IMAGE-%s] image-gen returned %s", num, img_resp.status_code)
            return None

        tmp_path = await _resolve_gen_response(img_resp, image_gen_url=image_gen_url)
        logger.info("  [IMAGE-%s] image-gen generated: %s", num, os.path.basename(tmp_path))

        # Step 3: R2 upload, with local-path fallback.
        return await _upload_to_r2_with_fallback(tmp_path, site_config=site_config)
    except Exception as err:
        logger.warning("  [IMAGE-%s] image-gen inline failed: %s", num, err)
        return None


async def _resolve_gen_response(
    img_resp: httpx.Response, *, image_gen_url: str,
) -> str:
    """Materialise an image-gen server response as a worker-local file path.

    The server either:
    - Returns JSON with ``filename``/``image_path``. We fetch the bytes
      back via ``GET <image_gen_url>/images/<filename>`` rather than trusting
      the path — image-gen and the worker run in separate containers as
      ``appuser`` whose in-container ``$HOME`` is ephemeral and not
      reliably bind-mount-shared. Closes Glad-Labs/poindexter#459.
    - Returns raw image bytes (older code path, kept for compatibility).

    Raises RuntimeError on any other response shape.
    """
    ct = img_resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = img_resp.json()
        filename = data.get("filename") or os.path.basename(
            data.get("image_path", "") or "",
        )
        if not filename:
            raise RuntimeError(
                "image-gen returned JSON without filename / image_path",
            )
        return await _download_gen_image(image_gen_url, filename)

    if ct.startswith("image/"):
        return _write_bytes_to_tempfile(img_resp.content)

    raise RuntimeError(f"image-gen returned unexpected content-type: {ct}")


def _generated_images_dir() -> str:
    """Worker-local directory for materialised image-gen bytes.

    Matches the path fragment that ``_upload_to_r2_with_fallback`` keys
    on (``/glad-labs-generated-images/``) so the post-R2 local-serve URL
    rewrite continues to work when R2 is unavailable.
    """
    output_dir = os.path.join(
        os.path.expanduser("~"), "Downloads", "glad-labs-generated-images",
    )
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _write_bytes_to_tempfile(content: bytes) -> str:
    """Persist image bytes to the worker-local generated-images dir."""
    with tempfile.NamedTemporaryFile(
        suffix=".png", delete=False, dir=_generated_images_dir(),
    ) as tmp:
        tmp.write(content)
        return tmp.name


async def _download_gen_image(image_gen_url: str, filename: str) -> str:
    """GET the bytes from the image-gen server's ``/images/<filename>`` and save.

    Avoids the filesystem coupling between the image-gen and worker
    containers — the image-gen server already exposes its outputs over HTTP
    (see ``scripts/image-gen-server.py``'s ``GET /images/{filename}``).
    """
    safe_name = os.path.basename(filename)
    url = f"{image_gen_url.rstrip('/')}/images/{safe_name}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
    ) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(
            f"image-gen /images returned {resp.status_code} for {safe_name}",
        )
    return _write_bytes_to_tempfile(resp.content)


async def _upload_to_r2_with_fallback(
    tmp_path: str, *, site_config: Any = None,
) -> str:
    """Upload the image to R2 and return a public URL, or fall back to a local path.

    If R2 upload succeeds, the local file is cleaned up. Otherwise the
    local path is rewritten to the worker's serve path (``/images/generated/...``)
    so the final URL still resolves for anyone viewing the post.
    """
    img_url = tmp_path
    try:
        from services.r2_upload_service import R2UploadService
        if site_config is None:
            raise RuntimeError(
                "R2 upload requires site_config; stage execute() must "
                "thread site_config from context (GH#95 / DI PR 4)",
            )
        svc = R2UploadService(site_config=site_config)
        # R2UploadService converts PNG→WebP and rewrites the key extension
        # automatically (poindexter#732); the .png here is the local temp
        # file extension, not the final R2 key suffix.
        r2_key = f"images/inline/{uuid.uuid4().hex[:12]}.png"
        r2_url = await svc.upload_to_r2(tmp_path, r2_key, content_type="image/png")
        if r2_url:
            img_url = r2_url
            with suppress(OSError):
                os.remove(tmp_path)  # best-effort cleanup
    except Exception:
        logger.debug("[IMAGE] R2 upload failed for inline, using local path")

    # Rewrite local-dir paths to the worker's serve URL.
    if img_url.startswith("/") and "/glad-labs-generated-images/" in img_url:
        img_url = f"/images/generated/{os.path.basename(img_url)}"
    return img_url


async def _try_pexels(
    search_query: str,
    topic: str,
    image_service: Any,
) -> tuple[str, str] | None:
    """Return ``(url, photographer)`` for a Pexels image, or None."""
    search_words = search_query.split()[:5]
    short_query = " ".join(search_words)
    keywords = [topic.split()[0]] if topic and topic.strip() else []
    try:
        img = await image_service.search_featured_image(
            topic=short_query, keywords=keywords,
        )
        if img and img.url:
            photographer = getattr(img, "photographer", "Pexels")
            return img.url, photographer
    except Exception as e:
        logger.exception("Pexels search failed: %s", e)
    return None


def _inject_html_image(
    content_text: str,
    num: str,
    img_url: str,
    alt_text: str,
    *,
    width: int,
    height: int,
) -> str:
    """Replace the numbered placeholder with an <img> tag."""
    replacement = (
        f'\n\n<img src="{img_url}" alt="{alt_text}" '
        f'width="{width}" height="{height}" loading="lazy" />\n\n'
    )
    return re.sub(
        rf"\[IMAGE-{num}[^\]]*\]", replacement, content_text, count=1,
    )


def _cleanup_leaked_descriptions(content_text: str) -> str:
    """Strip LLM-artifact lines that sometimes accompany image placeholders."""
    # Pattern 1: `: *description*` right after an image
    content_text = re.sub(
        r'(!\[[^\]]*\]\([^\)]+\))\s*\n\s*:\s+[^\n]+', r'\1', content_text,
    )
    # Pattern 2: standalone `*A description...*` or `*Imagine a...*`
    content_text = re.sub(
        r'\n\s*\*(?:A |An |Imagine |Visual |The |Split|Close)[^*]{40,}\*\s*\n',
        '\n', content_text,
    )
    # Pattern 3: unclosed `*A description...` — cap at next blank line
    content_text = re.sub(
        r'\n\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*\n]{40,}(?=\n\n)',
        '', content_text,
    )
    # Photo attribution lines
    content_text = re.sub(
        r'\n\s*\*?Photo by [^\n]+(?:Pexels|Unsplash|Pixabay)\*?\s*\n',
        '\n', content_text, flags=re.IGNORECASE,
    )
    return content_text
