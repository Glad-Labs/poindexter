"""Inline-image helper library — owns the image-gen / Pexels / injection logic.

This is the ``_``-prefixed library home (per the atom-independence convention —
the atom registry skips ``_`` modules, so this is a library, not a graph node)
for the inline-image machinery: Image Decision Agent placeholder planning,
image-gen prompt build + render, Pexels fallback, R2 upload, and HTML injection.

The three inline-image atoms (``content.plan_image_markers`` /
``content.generate_images`` / ``content.inject_images``) and the image-rebuild
atoms import the public-named helpers from here.

The bodies moved here (``git mv`` of ``stages/replace_inline_images.py``, then
the class carved back out) as the 2026-07 image half of the atom-independence
burn-down — so ``modules/content/image_helpers.py`` no longer needs to reach
into a stage to re-export them (baseline ``image_helpers.py`` 7 → gone). The
legacy ``ReplaceInlineImagesStage`` that used to delegate to these helpers was
a decomposed-by-#362 zombie (on no graph_def) and was deleted 2026-07.

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
"""

from __future__ import annotations

import asyncio
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

from services.image_prompt_sanitizer import clean_image_prompt
from utils.exception_format import describe_exception
from utils.findings import emit_finding

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


def _first_prose_end(content_text: str) -> int:
    """Index just past the article's first real paragraph of prose.

    "Prose" excludes headings, blank lines, and existing image markers — so for
    a draft that opens ``## Title\\n\\nWe hit a wall...`` this returns the offset
    at the end of "We hit a wall...", not the gap under the heading.

    Used as a floor on inline-image placement. The top of the article belongs to
    the hero image; an inline illustration above the first sentence reads as the
    hero and leaves the real hero slot looking empty (RCA 2026-07-31 — 10 of 82
    posts in a 60-day window opened with an inline image and zero prose above
    it). Returns 0 when the body is nothing but headings, which leaves placement
    unchanged rather than inventing a floor.
    """
    for match in re.finditer(r"(?m)^(?!\s*$)(?!#{1,6}\s)(?!\[IMAGE).+$", content_text):
        line = match.group().strip()
        # Skip pseudo-heading lines (``**Section Title**``) — they're structure,
        # not prose, and _BOLD_HEADING_RE already treats them as anchors.
        if _BOLD_HEADING_RE.match(line):
            continue
        para_end = content_text.find("\n\n", match.end())
        return len(content_text) if para_end < 0 else para_end
    return 0


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


def _default_render_timeout() -> int:
    """Declared default for ``image_render_timeout_seconds``.

    Read from ``settings_defaults`` rather than repeated as a literal — this key
    had drifted to four different values across the codebase, each a fossil of
    the default on the day its call site was written (2026-07-31).
    """
    from services.settings_defaults import default_int

    return default_int("image_render_timeout_seconds")


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
    search_query: str, style: str,
) -> str:
    """LLM instruction for an inline image-gen prompt.

    The wording lives in the ``image.inline_illustration`` skill prompt
    (UnifiedPromptManager: Langfuse override → skill YAML default), so it's
    tunable without a code edit. Falls back to a de-funnelled instruction that
    demands a concrete scene rendered in the chosen art style (replacing the
    old "describe a specific scene" line that produced literal tech slop).
    #image-zimage-and-variety. Deliberately takes no ``topic`` — handing the
    raw article title to this instruction let proper nouns/product names in
    the title get echoed into the rendered image as garbled text.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(
            "image.inline_illustration",
            search_query=search_query, style=style,
        )
    except Exception:  # noqa: BLE001 — prompt resolution is best-effort
        return (
            f"Write a Stable Diffusion XL image prompt for a {style} blog "
            f"illustration depicting a concrete, specific scene about: "
            f"{search_query}. Commit to the named art "
            "style. People are fine when the subject involves them — "
            "stylized, never photoreal. No text or lettering. 1 sentence. "
            "Output ONLY the prompt."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_from_router(text: str) -> str:
    """Proxy to :func:`services.text_utils.normalize_text`.

    Kept as a local helper so the inline-image atom call sites stay readable
    (``_normalize_from_router(content_text)``); lazy import preserves lock-free
    startup.
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
        # Empty plan: honest "no images" (None) vs a failed planner, which the
        # atom records as 2c_image_agent_error + a finding. Identical before
        # 2026-08-23, so a week of planner timeouts read as "no images".
        err = getattr(plan, "error", "")
        return content_text, ({"agent_error": err} if err else None)

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

    # Floor for inline placement: the top of the article is the HERO's slot.
    # ``para_end`` below resolves to the gap between a heading and its first
    # paragraph, which is the right look for a mid-article section but puts an
    # illustration above the opening sentence when the writer leads with an H2
    # instead of an intro paragraph. That image then reads as the hero while the
    # real hero slot looks empty — the visible half of the 2026-07-31 hero-image
    # RCA. Clamping (rather than dropping) keeps the image, just below the lede.
    prose_floor = _first_prose_end(content_text)

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
                # Never above the lede — see prose_floor above.
                para_end = max(para_end, prose_floor)
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
        emit_finding(
            source="content.image_helpers",
            kind="media_asset_recorder_unavailable",
            title="media_asset_recorder import failed — inline image not recorded",
            body=(
                "Deferred import of services.media_asset_recorder failed: "
                f"{describe_exception(exc)}. The inline image is not tracked in media_assets "
                "(cleanup / retention / cost-attribution can't see it); the "
                "rendered-HTML backfill is the fallback."
            ),
            dedup_key="media_asset_recorder_unavailable",
        )
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


async def _try_image_gen(
    num: str,
    search_query: str,
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
        image_gen_url = site_config.get("image_gen_server_url", "http://image-gen-server:9836")
        model = site_config.get("inline_image_prompt_model", "llama3:latest")
        inline_style = random.choice(_load_inline_styles(site_config))
        img_prompt_req = _build_inline_prompt_instruction(
            search_query, inline_style,
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
            # Drop any planning scaffolding the instruct model wrapped its
            # answer in — the >20-char guard below waves a bulleted
            # "Subject:/Constraints:/Output ONLY" plan straight through to the
            # renderer. See services.image_prompt_sanitizer (poindexter#3229).
            img_gen_prompt = clean_image_prompt(
                (getattr(result, "text", "") or "").strip().strip('"')
            )
        img_gen_prompt = _apply_base_style(img_gen_prompt, site_config)

        if not img_gen_prompt or len(img_gen_prompt) <= 20:
            return None

        logger.info("  [IMAGE-%s] image-gen prompt: %s...", num, img_gen_prompt[:60])

        # Step 2: image-gen renders the image
        neg_prompt = _get_image_gen_negative_prompt(site_config)
        _render_default = _default_render_timeout()
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", _render_default)
            if site_config is not None else _render_default
        )
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
                        "task_id": str(task_id) if task_id else None,
                    },
                    timeout=render_timeout,
                )

        if img_resp.status_code != 200:
            from services.image_ocr_gate import (
                describe_ocr_gate_rejection,
                is_ocr_gate_rejection,
                safe_json,
            )
            body = safe_json(img_resp)
            if is_ocr_gate_rejection(img_resp.status_code, body):
                # A verdict, not a window — the server already re-rolled the
                # seed up to image_ocr_gate_max_attempts times.
                logger.warning(
                    "  [IMAGE-%s] %s", num, describe_ocr_gate_rejection(body),
                )
            else:
                logger.warning("  [IMAGE-%s] image-gen returned %s", num, img_resp.status_code)
            return None

        tmp_path = await _resolve_gen_response(img_resp, image_gen_url=image_gen_url)
        logger.info("  [IMAGE-%s] image-gen generated: %s", num, os.path.basename(tmp_path))

        # Step 3: R2 upload, with local-path fallback.
        return await _upload_to_r2_with_fallback(tmp_path, site_config=site_config)
    except Exception as err:
        logger.warning("  [IMAGE-%s] image-gen inline failed: %s", num, err)
        return None


def stock_fallback_enabled(site_config: Any, *, allow_stock: bool = False) -> bool:
    """Whether a failed image-gen render may fall back to stock photography.

    Default OFF (``image_stock_fallback_enabled``). Owned imagery is the brand
    asset — a stock photo dropped in silently is a downgrade the pipeline never
    disclosed, and it ran that way for weeks unnoticed. Kept as a setting rather
    than deleted so a fork that wants stock can just flip it on.

    ``allow_stock`` is the PER-RUN override: the image_rebuild path already
    takes an explicit operator opt-in (``poindexter tasks rebuild-images
    --allow-stock``), and that opt-in has to actually reach the generation
    sites. Without it the flag would only relax the rebuild GATE while the
    atoms silently refused to produce stock at all — a flag that no longer
    does what its help text says.

    Note this gates the FALLBACK only. Stock chosen deliberately — the video
    director picking Pexels for a shot that needs real photography — is a
    different path and is unaffected.
    """
    if allow_stock:
        return True
    if site_config is None:
        return False
    return site_config.get_bool("image_stock_fallback_enabled", False)


async def _render_one_with_retry(
    client: httpx.AsyncClient,
    *,
    num: str,
    prompt: str,
    neg_prompt: str,
    image_gen_url: str,
    task_id: str | None,
    render_timeout: int,
    site_config: Any,
    attempts: int,
) -> str | None:
    """POST one /generate, retrying transient failures. None = give up.

    Worth retrying because the common failures are windows, not verdicts:
    image-gen exits and restarts (cold start + lazy model reload) for a VRAM
    reclaim, or the GPU lock times out under contention. Both clear in
    seconds. Before this, one unlucky POST silently became a stock photo.

    The retry is deliberately inside the caller's single ``image_gen`` GPU
    lock — re-acquiring per attempt would reintroduce the lock churn that
    poindexter#733/#841 removed.
    """
    last_err: str = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            img_resp = await client.post(
                f"{image_gen_url}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": neg_prompt,
                    "task_id": str(task_id) if task_id else None,
                },
                timeout=render_timeout,
            )
            if img_resp.status_code != 200:
                from services.image_ocr_gate import (
                    describe_ocr_gate_rejection,
                    is_ocr_gate_rejection,
                    safe_json,
                )
                body = safe_json(img_resp)
                if is_ocr_gate_rejection(img_resp.status_code, body):
                    # Terminal, unlike every other non-200 this loop retries:
                    # the server already spent image_ocr_gate_max_attempts
                    # re-rolls on this prompt, so another client attempt just
                    # buys a second full set of them for the same verdict.
                    logger.warning(
                        "  [IMAGE-%s] %s — not retrying (batch)",
                        num, describe_ocr_gate_rejection(body),
                    )
                    return None
                last_err = f"HTTP {img_resp.status_code}"
                logger.warning(
                    "  [IMAGE-%s] image-gen returned %s (batch, attempt %d/%d)",
                    num, img_resp.status_code, attempt, attempts,
                )
            else:
                tmp_path = await _resolve_gen_response(
                    img_resp, image_gen_url=image_gen_url,
                )
                img_url = await _upload_to_r2_with_fallback(
                    tmp_path, site_config=site_config,
                )
                logger.info(
                    "  [IMAGE-%s] image-gen generated + uploaded (batch%s)",
                    num, f", attempt {attempt}" if attempt > 1 else "",
                )
                return img_url
        except Exception as err:  # noqa: BLE001 — one image must not stop the batch
            last_err = f"{type(err).__name__}: {err}"
            logger.warning(
                "  [IMAGE-%s] image-gen render failed (batch, attempt %d/%d): %s",
                num, attempt, attempts, err,
            )
        if attempt < attempts:
            backoff = site_config.get_float("image_gen_retry_backoff_seconds", 3.0) or 3.0
            await asyncio.sleep(backoff)

    logger.warning(
        "  [IMAGE-%s] image-gen exhausted %d attempt(s): %s", num, attempts, last_err,
    )
    return None


async def _batch_generate_inline_image_urls(
    placeholders: list[tuple[str, str]],
    *,
    site_config: Any,
    task_id: str | None,
    platform: Any,
) -> list[str | None]:
    """Render image-gen images for ALL placeholders using two batched GPU locks.

    poindexter#733 / poindexter#841 — kills the per-placeholder Ollama↔image-gen
    swap churn on multi-image posts. The per-plan path (one ``_try_image_gen``
    call each) took the GPU ``2N`` times — one ``ollama`` lock for the prompt +
    one ``image_gen`` lock for the render, per image, reloading the model between
    each (~95 s/stage of churn). This takes it exactly twice regardless of N:

    * Phase 1 (one ``ollama`` lock): build ALL image-gen prompts sequentially.
    * Phase 2 (one ``image_gen`` lock): render ALL images sequentially.

    Returns a URL (or ``None`` on failure) per entry, in ``placeholders`` order.
    A per-image failure never stops the others — a ``None`` triggers the Pexels
    fallback in the caller (``content.generate_images``). Both locks carry
    ``task_id`` so ``gpu_task_sessions`` cost attribution survives (#157).

    Returns all-``None`` when the batch can't run at all: no DB pool (tests /
    bootstrap), no platform handle, or either GPU lock acquisition fails — the
    caller then Pexels-falls-back every image.
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

    image_gen_url = site_config.get("image_gen_server_url", "http://image-gen-server:9836")
    model = site_config.get("inline_image_prompt_model", "llama3:latest")
    neg_prompt = _get_image_gen_negative_prompt(site_config)

    # ------------------------------------------------------------------ #
    # Phase 1: build ALL prompts under a single Ollama lock              #
    # ------------------------------------------------------------------ #
    img_gen_prompts: list[str | None] = []
    try:
        async with gpu.lock(
            "ollama", model=model, task_id=task_id, phase="inline_image_prompt_batch",
        ):
            for num, desc in placeholders:
                if not desc:
                    # No writer-provided subject — fall back to Pexels rather
                    # than handing image-gen the raw article topic. A title
                    # containing a proper noun/product name gets echoed into
                    # the rendered image as garbled text.
                    logger.info(
                        "  [IMAGE-%s] no writer-provided subject — Pexels fallback", num,
                    )
                    img_gen_prompts.append(None)
                    continue
                search_query = desc.strip()
                inline_style = random.choice(_load_inline_styles(site_config))
                img_prompt_req = _build_inline_prompt_instruction(
                    search_query, inline_style,
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
                    # Same scaffolding strip as the per-image path above.
                    img_gen_prompt = clean_image_prompt(
                        (getattr(result, "text", "") or "").strip().strip('"')
                    )
                    img_gen_prompt = _apply_base_style(img_gen_prompt, site_config)
                    if img_gen_prompt and len(img_gen_prompt) > 20:
                        logger.info(
                            "  [IMAGE-%s] image-gen prompt (batch): %s...",
                            num, img_gen_prompt[:60],
                        )
                        img_gen_prompts.append(img_gen_prompt)
                    else:
                        logger.warning(
                            "  [IMAGE-%s] image-gen prompt too short/empty — Pexels fallback", num,
                        )
                        img_gen_prompts.append(None)
                except Exception as err:
                    logger.warning("  [IMAGE-%s] image-gen prompt generation failed: %s", num, err)
                    img_gen_prompts.append(None)
    except Exception as err:
        logger.warning("[IMAGE-BATCH] Ollama lock acquire failed: %s — falling back per-image", err)
        return [None] * n

    # ------------------------------------------------------------------ #
    # Phase 2: render ALL images under a single image-gen lock           #
    # ------------------------------------------------------------------ #
    image_gen_urls: list[str | None] = []
    render_timeout = site_config.get_int(
        "image_render_timeout_seconds", _default_render_timeout(),
    )
    gpu_model_label = site_config.get("image_generation_model", "image_gen")
    attempts = max(1, site_config.get_int("image_gen_render_attempts", 2))
    try:
        async with gpu.lock(
            "image_gen", model=gpu_model_label, task_id=task_id, phase="inline_image_batch",
        ):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(render_timeout, connect=5.0),
            ) as client:
                for (num, _desc), img_gen_prompt in zip(
                    placeholders, img_gen_prompts, strict=False,
                ):
                    if img_gen_prompt is None:
                        image_gen_urls.append(None)
                        continue
                    # Retry before conceding the image. The dominant failure is
                    # transient — image-gen restarting (cold start + lazy model
                    # reload) or a GPU lock timeout — so a single retry recovers
                    # most of what used to silently become a stock photo.
                    img_url = await _render_one_with_retry(
                        client, num=num, prompt=img_gen_prompt,
                        neg_prompt=neg_prompt, image_gen_url=image_gen_url,
                        task_id=task_id, render_timeout=render_timeout,
                        site_config=site_config, attempts=attempts,
                    )
                    image_gen_urls.append(img_url)
    except Exception as err:
        logger.warning(
            "[IMAGE-BATCH] image-gen lock acquire failed: %s — no image-gen images this run", err,
        )
        return [None] * n

    return image_gen_urls


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
    upload_error: Exception | None = None
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
    except Exception as exc:  # noqa: BLE001 — never block the image block
        upload_error = exc

    # Rewrite local-dir paths to the worker's serve URL.
    if img_url.startswith("/") and "/glad-labs-generated-images/" in img_url:
        img_url = f"/images/generated/{os.path.basename(img_url)}"

    if upload_error is not None:
        # Loud on purpose: the fallback URL resolves on the worker and ONLY on
        # the worker, so every in-process check still passes — url_validation
        # runs before the image block injects anything, and qa.vision fetches
        # from this same host where the file genuinely exists. The post ships
        # clean and the first observer of the broken image is a public reader.
        logger.warning(
            "[IMAGE] R2 upload failed (%s: %s) — this image falls back to the "
            "worker-local path %r, which does NOT resolve for public readers. "
            "No QA rail catches this: url_validation runs before the image "
            "block and qa.vision resolves the path on this worker.",
            type(upload_error).__name__, upload_error, img_url,
        )
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


def _replace_image_marker(content_text: str, num: str, replacement: str) -> str:
    """Substitute the first ``[IMAGE-<num> ...]`` marker, whitespace included.

    The marker normally sits on its own line, so it already carries blank
    lines on both sides. The pattern consumes that surrounding whitespace so
    the replacement fully owns its spacing — padding without consuming left
    3–5 blank lines around every injected image (poindexter#1006). The
    replacement is passed as a callable so URLs/alt text containing regex
    escape sequences (``\\1``, ``\\g``) are inserted literally instead of
    crashing ``re.sub``.
    """
    return re.sub(
        rf"[ \t]*\n*[ \t]*\[IMAGE-{num}[^\]]*\][ \t]*\n*",
        lambda _m: replacement,
        content_text,
        count=1,
    )


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
    return _replace_image_marker(content_text, num, replacement)


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


# ---------------------------------------------------------------------------
# Public seam — the inline-image atoms import these names (the underscore
# originals stay for the legacy stage + the existing test patch targets).
# ---------------------------------------------------------------------------

batch_generate_inline_image_urls = _batch_generate_inline_image_urls
cleanup_leaked_descriptions = _cleanup_leaked_descriptions
inject_html_image = _inject_html_image
normalize_from_router = _normalize_from_router
plan_and_inject_placeholders = _plan_and_inject_placeholders
record_inline_image_asset = _record_inline_image_asset
replace_image_marker = _replace_image_marker
try_image_gen = _try_image_gen
try_pexels = _try_pexels

__all__ = [
    "batch_generate_inline_image_urls",
    "cleanup_leaked_descriptions",
    "inject_html_image",
    "normalize_from_router",
    "plan_and_inject_placeholders",
    "record_inline_image_asset",
    "replace_image_marker",
    "stock_fallback_enabled",
    "try_image_gen",
    "try_pexels",
]
