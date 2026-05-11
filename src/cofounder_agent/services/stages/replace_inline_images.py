"""ReplaceInlineImagesStage — stage 2C of the content pipeline.

Finds / injects [IMAGE-N] placeholders in the draft and replaces each
with either an SDXL-generated image or a Pexels photo. Post-processes
the draft to strip any leaked SDXL prompt text.

Fully ported from ``_stage_replace_inline_images`` in content_router_service.py.
No more thin-wrapper delegation. Helper logic is split into internal
methods + module-level functions for readability; legacy behavior is
preserved byte-for-byte.

## Strategy (per placeholder)

1. **SDXL (primary)** — Ollama generates a prompt (random inline style),
   then the SDXL server renders. Path traversal guard on the returned
   path; R2 upload with local-path fallback if R2 unavailable.
2. **Pexels (fallback)** — used when SDXL fails, the server returns
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


INLINE_STYLES: tuple[str, ...] = (
    "photorealistic scene, cinematic lighting",
    "isometric 3D illustration, clean vector style, soft shadows",
    "dark moody editorial photograph, dramatic lighting",
    "clean minimal flat design, pastel colors, geometric shapes",
    "macro close-up photograph, extreme detail, bokeh",
)


SDXL_NEGATIVE_PROMPT = (
    "text, words, letters, watermark, face, person, hands, blurry, "
    "low quality, distorted, ugly, deformed"
)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class ReplaceInlineImagesStage:
    name = "replace_inline_images"
    description = "Decide + generate inline images (SDXL primary, Pexels fallback)"
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
        image_service = context.get("image_service") or get_image_service()
        category = context.get("category", "technology")
        # DI seam (glad-labs-stack#330) — content_router_service seeds
        # site_config into the stage context. Tests pass a mock SiteConfig.
        site_config = context.get("site_config")

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

        stages = context.setdefault("stages", {})
        updates: dict[str, Any] = {}

        # Look for existing placeholders from the writer; otherwise ask
        # the Image Decision Agent to plan + inject them.
        placeholders = _PLACEHOLDER_RE.findall(content_text)
        if not placeholders:
            content_text, plan = await _plan_and_inject_placeholders(
                content_text, topic, category,
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
        for num, desc in placeholders:
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
) -> tuple[str, dict[str, Any] | None]:
    """Ask the Image Decision Agent to decide + inject [IMAGE-N] placeholders.

    Returns ``(content_text, info)`` where info may carry a
    ``featured_image_plan`` (if the agent recommends one) or an
    ``agent_error`` string (if the decision agent crashed).
    """
    try:
        from services.image_decision_agent import plan_images
    except Exception as e:
        logger.exception("[IMAGE_AGENT] Image Decision Agent FAILED to import: %s", e)
        return content_text, {"agent_error": str(e)}

    try:
        plan = await plan_images(content_text, topic, category, max_images=3)
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
    headings = list(_HEADING_RE.finditer(content_text))
    heading_map = {
        re.sub(r"^#+\s*", "", h.group()).strip().lower(): h for h in headings
    }

    insert_positions: list[tuple[int, int, str, str]] = []
    for i, img in enumerate(plan.images):
        for heading_text, h_match in heading_map.items():
            if (
                img.section_heading.lower() in heading_text
                or heading_text in img.section_heading.lower()
            ):
                para_end = content_text.find("\n\n", h_match.end())
                if para_end > 0:
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
) -> str:
    """Replace one ``[IMAGE-N]`` placeholder with a real image or strip it.

    ``task_id`` is forwarded to :func:`_try_sdxl` so the GPU scheduler
    can attribute Ollama-prompt + SDXL-render electricity cost back to
    the originating pipeline task — see Glad-Labs/poindexter#157.

    ``post_id`` is forwarded so a successful image generation lands a
    ``media_assets`` row pinned to the post it belongs to (Glad-Labs/
    poindexter#161). When ``post_id`` is None (early-pipeline calls
    before the post is persisted), the row is skipped.
    """
    from services.alt_text import sanitize_alt_text
    search_query = desc.strip() if desc else topic
    alt_text = desc.strip() if desc else f"{topic} illustration"
    # Normalize structural artefacts of the placeholder format.
    alt_text = alt_text.replace("[", "").replace("]", "").replace("\n", " ")
    alt_text = re.sub(r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", alt_text).strip()
    # GH-84: strip ``||provider:hint||`` pipeline tokens + enforce a
    # DB-configurable budget with word-boundary truncation (no mid-word chop).
    # GH-469: pass topic so SDXL-prompt-shaped descriptors fall back to
    # a topic-derived alt instead of leaking imperative-mood prompt text.
    alt_text = sanitize_alt_text(
        alt_text,
        budget=(
            site_config.get_int("alt_text_budget", 120)
            if site_config is not None else 120
        ),
        topic=topic,
    )

    # Strategy 1: SDXL.
    img_url = await _try_sdxl(
        num, search_query, topic, site_config=site_config, task_id=task_id,
    )
    if img_url and img_url not in used_image_ids:
        used_image_ids.add(img_url)
        content_text = _inject_html_image(
            content_text, num, img_url, alt_text,
            width=1024, height=1024,
        )
        logger.info("  [IMAGE-%s] SDXL generated + R2 uploaded", num)
        await _record_inline_image_asset(
            site_config=site_config,
            post_id=post_id,
            public_url=img_url,
            provider_plugin="image.sdxl",
            width=1024,
            height=1024,
            mime_type="image/png",
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


async def _try_sdxl(
    num: str,
    search_query: str,
    topic: str,
    *,
    site_config: Any,
    task_id: str | None,
) -> str | None:
    """Generate an SDXL image and return its final URL (R2 or local).

    ``task_id`` is threaded through to :meth:`gpu.lock` for both the
    Ollama prompt-build and the SDXL render so ``gpu_task_sessions`` /
    cost_logs rows attribute kWh + electricity cost to the originating
    pipeline task. Without this, the inline-image phase logged un-
    attributed sessions — see Glad-Labs/poindexter#157.
    """
    from services.gpu_scheduler import gpu

    try:
        sdxl_url = site_config.get("sdxl_server_url", "http://host.docker.internal:9836")
        ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
        model = site_config.get("inline_image_prompt_model", "llama3:latest")
        inline_style = random.choice(INLINE_STYLES)
        img_prompt_req = (
            f"Write a Stable Diffusion XL image prompt for a blog illustration about: {search_query}\n"
            f"Article topic: {topic}\n\n"
            f"Requirements: {inline_style}, no people, no text, no faces. "
            "Describe a specific scene. 1 sentence only. Output ONLY the prompt."
        )

        # Step 1: ollama generates the SDXL prompt
        async with gpu.lock(
            "ollama", model=model, task_id=task_id, phase="inline_image_prompt",
        ):
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(f"{ollama_url}/api/generate", json={
                    "model": model, "prompt": img_prompt_req, "stream": False,
                    "options": {"num_predict": 100, "temperature": 0.8, "num_ctx": 4096},
                })
                resp.raise_for_status()
                sdxl_prompt = resp.json().get("response", "").strip().strip('"')

        if not sdxl_prompt or len(sdxl_prompt) <= 20:
            return None

        logger.info("  [IMAGE-%s] SDXL prompt: %s...", num, sdxl_prompt[:60])

        # Step 2: SDXL renders the image
        async with gpu.lock(
            "sdxl", model="sdxl_lightning",
            task_id=task_id, phase="inline_image",
        ):
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
                img_resp = await client.post(
                    f"{sdxl_url}/generate",
                    json={
                        "prompt": sdxl_prompt,
                        "negative_prompt": SDXL_NEGATIVE_PROMPT,
                        "steps": 8, "guidance_scale": 2.0,
                    },
                    timeout=60,
                )

        if img_resp.status_code != 200:
            logger.warning("  [IMAGE-%s] SDXL returned %s", num, img_resp.status_code)
            return None

        tmp_path = await _resolve_sdxl_response(img_resp, sdxl_url=sdxl_url)
        logger.info("  [IMAGE-%s] SDXL generated: %s", num, os.path.basename(tmp_path))

        # Step 3: R2 upload, with local-path fallback.
        return await _upload_to_r2_with_fallback(tmp_path)
    except Exception as err:
        logger.warning("  [IMAGE-%s] SDXL inline failed: %s", num, err)
        return None


async def _resolve_sdxl_response(
    img_resp: httpx.Response, *, sdxl_url: str,
) -> str:
    """Materialise an SDXL server response as a worker-local file path.

    The server either:
    - Returns JSON with ``filename``/``image_path``. We fetch the bytes
      back via ``GET <sdxl_url>/images/<filename>`` rather than trusting
      the path — SDXL and the worker run in separate containers as
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
                "SDXL returned JSON without filename / image_path",
            )
        return await _download_sdxl_image(sdxl_url, filename)

    if ct.startswith("image/"):
        return _write_bytes_to_tempfile(img_resp.content)

    raise RuntimeError(f"SDXL returned unexpected content-type: {ct}")


def _generated_images_dir() -> str:
    """Worker-local directory for materialised SDXL bytes.

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


async def _download_sdxl_image(sdxl_url: str, filename: str) -> str:
    """GET the bytes from the SDXL server's ``/images/<filename>`` and save.

    Avoids the filesystem coupling between the SDXL and worker
    containers — the SDXL server already exposes its outputs over HTTP
    (see ``scripts/sdxl-server.py``'s ``GET /images/{filename}``).
    """
    safe_name = os.path.basename(filename)
    url = f"{sdxl_url.rstrip('/')}/images/{safe_name}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
    ) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(
            f"SDXL /images returned {resp.status_code} for {safe_name}",
        )
    return _write_bytes_to_tempfile(resp.content)


async def _upload_to_r2_with_fallback(tmp_path: str) -> str:
    """Upload the image to R2 and return a public URL, or fall back to a local path.

    If R2 upload succeeds, the local file is cleaned up. Otherwise the
    local path is rewritten to the worker's serve path (``/images/generated/...``)
    so the final URL still resolves for anyone viewing the post.
    """
    img_url = tmp_path
    try:
        from services.r2_upload_service import upload_to_r2
        r2_key = f"images/inline/{uuid.uuid4().hex[:12]}.png"
        r2_url = await upload_to_r2(tmp_path, r2_key, content_type="image/png")
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
