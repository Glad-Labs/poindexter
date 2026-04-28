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
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        database_service = context.get("database_service")
        image_service = context.get("image_service") or get_image_service()
        category = context.get("category", "technology")

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

        # Phase H step 5 (GH#95): site_config is seeded on the pipeline
        # context by content_router_service. Tests build context dicts
        # with the fake site_config wired in explicitly.
        _sc = context["site_config"]

        stages = context.setdefault("stages", {})
        updates: dict[str, Any] = {}

        # Look for existing placeholders from the writer; otherwise ask
        # the Image Decision Agent to plan + inject them.
        placeholders = _PLACEHOLDER_RE.findall(content_text)
        if not placeholders:
            # Phase H step 5 (GH#95): thread site_config to the image
            # decision agent. Stages receive site_config via the pipeline
            # context dict (seeded by content_router_service step 4.1).
            _sc = context.get("site_config")
            if _sc is None:
                raise RuntimeError(
                    "replace_inline_images stage requires site_config in context — "
                    "context_router_service must seed it under 'site_config'"
                )
            content_text, plan = await _plan_and_inject_placeholders(
                content_text, topic, category, site_config=_sc,
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
                site_config=_sc,
                task_id=task_id,
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

    Args:
        site_config: SiteConfig instance threaded to ``plan_images``.
            Required — no module singleton fallback.
    """
    try:
        from services.image_decision_agent import plan_images
    except Exception as e:
        logger.exception("[IMAGE_AGENT] Image Decision Agent FAILED to import: %s", e)
        return content_text, {"agent_error": str(e)}

    try:
        plan = await plan_images(
            content_text, topic, category, max_images=3, site_config=site_config,
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
    site_config: Any,
    task_id: str,
) -> str:
    """Replace one ``[IMAGE-N]`` placeholder with a real image or strip it.

    ``task_id`` is forwarded to :func:`_try_sdxl` so the GPU scheduler
    can attribute Ollama-prompt + SDXL-render electricity cost back to
    the originating pipeline task — see Glad-Labs/poindexter#157.
    """
    from services.alt_text import sanitize_alt_text
    search_query = desc.strip() if desc else topic
    alt_text = desc.strip() if desc else f"{topic} illustration"
    # Normalize structural artefacts of the placeholder format.
    alt_text = alt_text.replace("[", "").replace("]", "").replace("\n", " ")
    alt_text = re.sub(r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", alt_text).strip()
    # GH-84: strip ``||provider:hint||`` pipeline tokens + enforce a
    # DB-configurable budget with word-boundary truncation (no mid-word chop).
    alt_text = sanitize_alt_text(
        alt_text,
        budget=site_config.get_int("alt_text_budget", 120),
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
            return content_text

    # Strategy 3: strip.
    content_text = re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
    logger.warning("  [IMAGE-%s] no image source available, removed placeholder", num)
    return content_text


async def _try_sdxl(
    num: str,
    search_query: str,
    topic: str,
    *,
    site_config: Any,
    task_id: str,
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

        tmp_path = _resolve_sdxl_response(img_resp, site_config=site_config)
        logger.info("  [IMAGE-%s] SDXL generated: %s", num, os.path.basename(tmp_path))

        # Step 3: R2 upload, with local-path fallback.
        return await _upload_to_r2_with_fallback(tmp_path, site_config=site_config)
    except Exception as err:
        logger.warning("  [IMAGE-%s] SDXL inline failed: %s", num, err)
        return None


def _resolve_sdxl_response(img_resp: httpx.Response, *, site_config: Any) -> str:
    """Decode the SDXL server's response to a local image path.

    The server either:
    - Returns JSON with ``image_path`` (when SDXL runs on the host and
      the worker runs in docker — we translate host paths to container
      paths using ``site_config.host_home``).
    - Returns raw image bytes (for setups without a shared filesystem).

    Raises RuntimeError on any other response shape or if the returned
    path escapes the allowed directories (path traversal guard).
    """
    ct = img_resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = img_resp.json()
        tmp_path = data.get("image_path", "")
        # Host → container path translation.
        host_home = site_config.get("host_home", "")
        if host_home and tmp_path.startswith(host_home):
            tmp_path = tmp_path.replace(host_home, os.path.expanduser("~"), 1)
        # Normalize Windows backslashes.
        tmp_path = tmp_path.replace("\\", "/")
        # Path traversal guard — SDXL response is external input. The
        # allowlist must cover the canonical container paths where SDXL
        # writes (shared volume mount at /root/.poindexter) AND the
        # current user's home (/home/appuser) for the legacy bytes-
        # content codepath below which writes to ~/Downloads. Hard-coding
        # /root/.poindexter vs using expanduser('~/.poindexter') matters
        # because the worker runs as appuser (uid 1001), not root —
        # ``~`` expands to /home/appuser, which is NOT where SDXL writes.
        allowed = [
            "/root/Downloads",
            "/root/.poindexter",
            os.path.realpath(os.path.expanduser("~/Downloads")),
            os.path.realpath(os.path.expanduser("~/.poindexter")),
        ]
        resolved = os.path.realpath(tmp_path)
        if not any(resolved.startswith(d) for d in allowed):
            raise RuntimeError(
                f"SDXL returned path outside allowed directories: "
                f"{os.path.basename(tmp_path)}"
            )
        if not tmp_path or not os.path.exists(tmp_path):
            raise RuntimeError(
                f"SDXL returned JSON but image_path missing or invalid: {tmp_path}"
            )
        return tmp_path

    if ct.startswith("image/"):
        output_dir = os.path.join(
            os.path.expanduser("~"), "Downloads", "glad-labs-generated-images",
        )
        os.makedirs(output_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False, dir=output_dir,
        ) as tmp:
            tmp.write(img_resp.content)
            return tmp.name

    raise RuntimeError(f"SDXL returned unexpected content-type: {ct}")


async def _upload_to_r2_with_fallback(tmp_path: str, *, site_config: Any) -> str:
    """Upload the image to R2 and return a public URL, or fall back to a local path.

    If R2 upload succeeds, the local file is cleaned up. Otherwise the
    local path is rewritten to the worker's serve path (``/images/generated/...``)
    so the final URL still resolves for anyone viewing the post.
    """
    img_url = tmp_path
    try:
        from services.r2_upload_service import upload_to_r2
        r2_key = f"images/inline/{uuid.uuid4().hex[:12]}.png"
        r2_url = await upload_to_r2(
            tmp_path, r2_key, content_type="image/png", site_config=site_config,
        )
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
