"""SourceFeaturedImageStage — stage 3 of the content pipeline.

Full port of ``_stage_source_featured_image`` from content_router_service.py
(no longer a thin wrapper). Tries SDXL editorial-illustration generation
first, falls back to a Pexels photo if SDXL is unavailable or fails.

## Strategy

1. **Early out** — if ``generate_featured_image`` is False in context,
   record the skip and return.
2. **SDXL editorial illustration** — the style is picked by a DB-driven
   rotation (``image_styles`` app_setting) that filters out the last 5
   published posts' styles and this worker's recent in-memory picks.
   An Ollama prompt build goes through first; SDXL server renders with
   the chosen style's palette/mood.
3. **R2 upload** — SDXL output uploads to R2 (replaced Cloudinary for
   cost reasons). Falls back to the worker's local serve path if R2
   fails.
4. **Pexels fallback** — if SDXL is unavailable, unreachable, or the
   pipeline errors at any point, the Pexels stock-photo search is used.
5. **Nothing found** — set ``stages["3_featured_image_found"] = False``
   and return None. Pipeline continues without a featured image.

## Context reads

- ``topic`` (str), ``tags`` (list[str])
- ``generate_featured_image`` (bool, default True)
- ``task_id`` (str)
- ``image_service`` (falls back to ``get_image_service()``)
- ``featured_image_prompt`` (optional — if set upstream, skips SDXL
  prompt generation and uses this verbatim)

## Context writes

- ``featured_image`` (GeneratedImage | Pexels image | None)
- ``featured_image_url`` (str)
- ``featured_image_alt`` (str, capped at 200 chars)
- ``featured_image_width``, ``featured_image_height`` (int)
- ``featured_image_photographer`` (str)
- ``featured_image_source`` (str: "sdxl_local" / "sdxl_cloudinary" / "pexels")
- ``image_style`` (str, set when SDXL's rotation picked one)
- ``stages["3_featured_image_found"]`` (bool)
- ``stages["3_image_source"]`` (str, "sdxl" or "pexels")
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


DEFAULT_STYLES: tuple[tuple[str, str], ...] = (
    (
        "flat vector illustration",
        "simple geometric shapes, cyan and dark navy, clean minimal, no text",
    ),
    (
        "cyberpunk neon style",
        "dark background, glowing cyan purple neon lines, futuristic, no text",
    ),
    (
        "isometric 3D illustration",
        "colorful clean technical, low angle, no text",
    ),
)


DEFAULT_NEGATIVE = (
    "text, words, letters, watermark, face, person, hands, blurry, "
    "low quality, distorted, ugly, deformed"
)


@dataclass
class GeneratedImage:
    """Return shape for an SDXL-generated featured image."""

    url: str
    photographer: str
    source: str


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class SourceFeaturedImageStage:
    name = "source_featured_image"
    description = "Source a featured image — SDXL primary, Pexels fallback"
    timeout_seconds = 300
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.image_service import get_image_service

        topic = context.get("topic", "")
        tags = context.get("tags") or []
        generate_featured_image = bool(context.get("generate_featured_image", True))
        task_id = context.get("task_id")
        post_id = context.get("post_id")
        image_service = context.get("image_service") or get_image_service()
        # DI seam (glad-labs-stack#330) — content_router_service seeds
        # site_config into the stage context.
        site_config = context.get("site_config")

        stages = context.setdefault("stages", {})

        if not generate_featured_image:
            stages["3_featured_image_found"] = False
            logger.info("Image search skipped (disabled)")
            return StageResult(
                ok=True,
                detail="disabled via generate_featured_image flag",
                context_updates={"stages": stages, "featured_image": None},
            )

        logger.info("STAGE 3: Sourcing featured image...")

        updates: dict[str, Any] = {"stages": stages}

        # Strategy 1: SDXL (only when the service is available).
        sdxl_attempted = (
            image_service.sdxl_available or not image_service.sdxl_initialized
        )
        if sdxl_attempted:
            # Pull / lazily-create the style-rotation tracker. Production
            # keeps one long-lived instance per worker process (stashed on
            # app.state by lifespan); tests inject their own mock; absent
            # context just builds a fresh one here.
            style_tracker = context.get("image_style_tracker")
            if style_tracker is None:
                from services.image_style_rotation import ImageStyleTracker
                style_tracker = ImageStyleTracker()

            sdxl_image = await _try_sdxl_featured(
                topic=topic,
                existing_prompt=context.get("featured_image_prompt", ""),
                task_id=task_id,
                on_style_picked=lambda s: updates.update({"image_style": s}),
                style_tracker=style_tracker,
                site_config=site_config,
            )
            if sdxl_image is not None:
                stages["3_featured_image_found"] = True
                stages["3_image_source"] = "sdxl"
                updates.update({
                    "featured_image": sdxl_image,
                    "featured_image_url": sdxl_image.url,
                    "featured_image_alt": f"{topic} — AI generated illustration"[:200],
                    "featured_image_width": 1024,
                    "featured_image_height": 1024,
                    "featured_image_photographer": sdxl_image.photographer,
                    "featured_image_source": sdxl_image.source,
                    "stages": stages,
                })
                # Glad-Labs/poindexter#161: record media_assets row for
                # cleanup / retention / cost-attribution. Best-effort —
                # never breaks the Stage.
                await _record_featured_image_asset(
                    site_config=site_config,
                    post_id=post_id,
                    public_url=sdxl_image.url,
                    width=1024,
                    height=1024,
                    provider_plugin=f"image.{sdxl_image.source}",
                    metadata={
                        "topic": topic,
                        "task_id": str(task_id or ""),
                        "photographer": sdxl_image.photographer,
                        "image_style": updates.get("image_style", ""),
                    },
                    mime_type="image/png",
                )
                logger.info("Featured image generated via SDXL + R2")
                return StageResult(
                    ok=True,
                    detail=f"sdxl: {sdxl_image.url[:60]}",
                    context_updates=updates,
                    metrics={"source": "sdxl"},
                )

        # Strategy 2: Pexels fallback.
        search_keywords = tags or [topic]
        try:
            pexels = await image_service.search_featured_image(
                topic=topic, keywords=search_keywords,
            )
            if pexels:
                stages["3_featured_image_found"] = True
                stages["3_image_source"] = "pexels"
                updates.update({
                    "featured_image": pexels,
                    "featured_image_url": pexels.url,
                    "featured_image_alt": (
                        f"{topic} — Photo by {pexels.photographer} on Pexels"[:200]
                    ),
                    "featured_image_width": getattr(pexels, "width", 650),
                    "featured_image_height": getattr(pexels, "height", 433),
                    "featured_image_photographer": pexels.photographer,
                    "featured_image_source": pexels.source,
                    "stages": stages,
                })
                # Glad-Labs/poindexter#161 — same insert as the SDXL
                # branch above; Pexels images need a media_assets row
                # too so backfill scripts can find them.
                await _record_featured_image_asset(
                    site_config=site_config,
                    post_id=post_id,
                    public_url=pexels.url,
                    width=getattr(pexels, "width", 650),
                    height=getattr(pexels, "height", 433),
                    provider_plugin="image.pexels",
                    metadata={
                        "topic": topic,
                        "task_id": str(task_id or ""),
                        "photographer": pexels.photographer,
                    },
                    mime_type="image/jpeg",
                )
                logger.info(
                    "Featured image found: %s (Pexels)", pexels.photographer,
                )
                return StageResult(
                    ok=True,
                    detail=f"pexels: {pexels.photographer}",
                    context_updates=updates,
                    metrics={"source": "pexels"},
                )
            stages["3_featured_image_found"] = False
            logger.warning("No featured image found for '%s'", topic)
        except Exception as e:
            logger.error("Image search failed: %s", e, exc_info=True)
            stages["3_featured_image_found"] = False

        updates["stages"] = stages
        updates.setdefault("featured_image", None)
        return StageResult(
            ok=True,
            detail="no image (SDXL unavailable + pexels returned none)",
            context_updates=updates,
            metrics={"source": "none"},
        )


# ---------------------------------------------------------------------------
# media_assets persistence (Glad-Labs/poindexter#161)
# ---------------------------------------------------------------------------


async def _record_featured_image_asset(
    *,
    site_config: Any,
    post_id: Any,
    public_url: str,
    width: int,
    height: int,
    provider_plugin: str,
    metadata: dict[str, Any],
    mime_type: str,
) -> None:
    """Best-effort ``media_assets`` insert for the featured image.

    Wraps :func:`services.media_asset_recorder.record_media_asset` so
    the call site stays one line and never propagates DB errors out
    of the Stage. Used by both the SDXL and Pexels success branches.
    """
    try:
        from services.media_asset_recorder import record_media_asset
    except Exception as exc:  # noqa: BLE001 — defensive import guard
        logger.debug(
            "[STAGE3] media_asset_recorder unavailable: %s", exc,
        )
        return
    pool = getattr(site_config, "_pool", None)
    storage_provider = (
        "cloudflare_r2"
        if public_url and public_url.startswith("http") and "r2" in public_url
        else ("local" if (public_url or "").startswith("/") else "external")
    )
    await record_media_asset(
        pool=pool,
        post_id=post_id,
        asset_type="featured_image",
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


# ---------------------------------------------------------------------------
# SDXL: prompt building + rendering + R2 upload
# ---------------------------------------------------------------------------


async def _try_sdxl_featured(
    topic: str,
    existing_prompt: str,
    task_id: str | None,
    on_style_picked: Any,  # callable that records the chosen style
    style_tracker: Any,    # ImageStyleTracker instance
    *,
    site_config: Any = None,
) -> GeneratedImage | None:
    """Full SDXL path: pick style → build prompt → render → upload to R2."""
    # site_config is the DI seam (glad-labs-stack#330) — passed by execute().
    try:
        negative = (
            site_config.get("image_negative_prompt", DEFAULT_NEGATIVE)
            if site_config is not None else DEFAULT_NEGATIVE
        )
        sdxl_prompt = existing_prompt
        if not sdxl_prompt:
            sdxl_prompt = await _build_sdxl_prompt(
                topic, on_style_picked, style_tracker, site_config=site_config,
            )

        sdxl_url = (
            site_config.get("sdxl_server_url", "http://host.docker.internal:9836")
            if site_config is not None
            else "http://host.docker.internal:9836"
        )
        output_path = await _render_sdxl(
            sdxl_url, sdxl_prompt, negative, task_id=task_id,
        )
        if output_path is None:
            return None

        image_url = await _upload_featured_to_r2(output_path, task_id)
        source = "sdxl_cloudinary" if "cloudinary" in image_url else "sdxl_local"
        return GeneratedImage(
            url=image_url,
            photographer="AI Generated (SDXL)",
            source=source,
        )
    except Exception as e:
        logger.info("SDXL generation skipped (%s), falling back to Pexels", e)
        return None


async def _build_sdxl_prompt(
    topic: str,
    on_style_picked: Any,
    style_tracker: Any,
    *,
    site_config: Any = None,
) -> str:
    """Pick a rotation style + ask Ollama for an editorial prompt."""
    styles = _load_styles_from_settings(site_config) or list(DEFAULT_STYLES)

    recent = await _load_recent_published_styles(site_config)
    mem_recent = style_tracker.recent()
    all_recent = set(recent) | set(mem_recent)

    available = [s for s in styles if s[0] not in all_recent] or styles
    chosen_style, style_tags = random.choice(available)
    style_tracker.record(chosen_style)
    on_style_picked(chosen_style)

    ollama_url = (
        site_config.get("ollama_base_url", "http://host.docker.internal:11434")
        if site_config is not None
        else "http://host.docker.internal:11434"
    )
    prompt_model = (
        site_config.get("inline_image_prompt_model", "llama3:latest")
        if site_config is not None else "llama3:latest"
    )
    img_prompt = (
        "Write a Stable Diffusion XL image prompt for a magazine-style editorial cover image.\n"
        f"The article is about: {topic}\n"
        "DO NOT depict the topic literally. Instead, create an atmospheric scene that evokes the FEELING of the topic.\n"
        f"Style direction: {chosen_style}\n\n"
        f"Requirements: {style_tags}, faceless silhouettes OK but no identifiable faces, "
        "no text or words in the image, no hands. "
        "Think editorial magazine art — mood, atmosphere, imagination. "
        "1-2 sentences only. Output ONLY the prompt, nothing else."
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=3.0)
        ) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": prompt_model, "prompt": img_prompt, "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.7, "num_ctx": 4096},
                },
                timeout=30,
            )
            resp.raise_for_status()
            prompt_text = resp.json().get("response", "").strip().strip('"')
        logger.info(
            "[IMAGE] Style: %s | SDXL prompt: %s", chosen_style, prompt_text[:80],
        )
        return prompt_text
    except Exception as e:
        logger.warning("[IMAGE] LLM prompt generation failed, using fallback: %s", e)
        return f"{chosen_style}, {style_tags}, no text, no faces"


def _load_styles_from_settings(site_config: Any = None) -> list[tuple[str, str]]:
    """Read app_settings.image_styles (JSON array of {scene, tags})."""
    if site_config is None:
        return []
    raw = site_config.get("image_styles", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return [(s["scene"], s["tags"]) for s in parsed if "scene" in s and "tags" in s]


async def _load_recent_published_styles(site_config: Any = None) -> list[str]:
    """Fetch the 5 most-recently-published posts' image_style from metadata."""
    if site_config is None:
        return []
    try:
        import asyncpg

        cloud_url = site_config.get("database_url", "")
        if not cloud_url:
            return []
        conn = await asyncpg.connect(cloud_url)
        try:
            rows = await conn.fetch("""
                SELECT metadata->>'image_style' as style
                FROM posts WHERE status = 'published'
                AND metadata->>'image_style' IS NOT NULL
                ORDER BY published_at DESC LIMIT 5
            """)
            return [r["style"] for r in rows if r["style"]]
        finally:
            await conn.close()
    except Exception:
        return []


async def _render_sdxl(
    sdxl_url: str,
    sdxl_prompt: str,
    negative_prompt: str,
    task_id: str | None = None,
) -> str | None:
    """Call the SDXL server and return the local path of the generated image."""
    from services.gpu_scheduler import gpu

    async with gpu.lock(
        "sdxl", model="sdxl_lightning",
        task_id=task_id, phase="featured_image",
    ):
        # 60s cap — Lightning is ~2s; headroom for cold load + upload.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0)
        ) as client:
            resp = await client.post(
                f"{sdxl_url}/generate",
                json={
                    "prompt": sdxl_prompt,
                    "negative_prompt": negative_prompt,
                    "steps": 8, "guidance_scale": 2.0,
                },
                timeout=60,
            )

    if resp.status_code != 200:
        return None

    return await _resolve_sdxl_featured_response(resp, sdxl_url=sdxl_url)


async def _resolve_sdxl_featured_response(
    resp: httpx.Response, *, sdxl_url: str,
) -> str | None:
    """Materialise the SDXL server's featured-image response on disk.

    Fetches bytes via ``GET <sdxl_url>/images/<filename>`` when SDXL
    returns JSON, rather than trusting the in-container path. The SDXL
    and worker containers both run as ``appuser`` with ephemeral
    in-container homes — the volume mount that was supposed to bridge
    them lands on ``/root/.poindexter/`` while the SDXL server writes
    to ``/home/appuser/.poindexter/``, so the worker never sees the
    file on disk. Closes Glad-Labs/poindexter#459.
    """
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = resp.json()
        filename = data.get("filename") or os.path.basename(
            data.get("image_path", "") or "",
        )
        gen_ms = data.get("generation_time_ms", 0)
        if not filename:
            logger.warning(
                "[IMAGE] SDXL returned JSON without filename / image_path",
            )
            return None
        try:
            output_path = await _download_featured_sdxl_image(
                sdxl_url, filename,
            )
        except Exception as e:
            logger.warning(
                "[IMAGE] SDXL /images fetch failed for %s: %s", filename, e,
            )
            return None
        logger.info(
            "[IMAGE] Featured SDXL generated: %s (%dms)",
            os.path.basename(output_path), gen_ms,
        )
        return output_path
    if ct.startswith("image/"):
        return _write_featured_bytes_to_tempfile(resp.content)
    return None


def _featured_generated_images_dir() -> str:
    """Worker-local directory for materialised SDXL bytes.

    Matches the path fragment that ``_upload_featured_to_r2``'s
    local-fallback URL rewrite keys on (``/glad-labs-generated-images/``),
    so the local-serve URL still resolves when R2 is unavailable.
    """
    output_dir = os.path.join(
        os.path.expanduser("~"), "Downloads", "glad-labs-generated-images",
    )
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _write_featured_bytes_to_tempfile(content: bytes) -> str:
    """Persist image bytes to the worker-local generated-images dir."""
    with tempfile.NamedTemporaryFile(
        suffix=".png", delete=False, dir=_featured_generated_images_dir(),
    ) as tmp:
        tmp.write(content)
        return tmp.name


async def _download_featured_sdxl_image(sdxl_url: str, filename: str) -> str:
    """GET the bytes from the SDXL server's ``/images/<filename>``.

    The SDXL server already serves its outputs over HTTP (see
    ``scripts/sdxl-server.py``'s ``GET /images/{filename}``), so the
    worker no longer needs the SDXL container's filesystem to be
    mounted in.
    """
    safe_name = os.path.basename(filename)
    url = f"{sdxl_url.rstrip('/')}/images/{safe_name}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
    ) as client:
        get_resp = await client.get(url)
    if get_resp.status_code != 200:
        raise RuntimeError(
            f"SDXL /images returned {get_resp.status_code} for {safe_name}",
        )
    return _write_featured_bytes_to_tempfile(get_resp.content)


async def _upload_featured_to_r2(output_path: str, task_id: str | None) -> str:
    """Upload the featured image to R2 and return the final URL."""
    try:
        from services.r2_upload_service import upload_to_r2
        r2_id = task_id or uuid.uuid4().hex[:12]
        r2_key = f"images/featured/{r2_id}.jpg"
        r2_url = await upload_to_r2(output_path, r2_key, content_type="image/jpeg")
        if r2_url:
            logger.info("Uploaded to R2: %s", r2_url[:80])
            with suppress(OSError):
                os.remove(output_path)  # best-effort cleanup
            return r2_url
        logger.warning("R2 upload returned None, using local path")
    except Exception as e:
        logger.warning("R2 upload failed (using local): %s", e)

    # Rewrite local-dir paths to the worker's serve URL.
    if (
        "/glad-labs-generated-images/" in output_path
        and not output_path.startswith("http")
    ):
        return f"/images/generated/{os.path.basename(output_path)}"
    return output_path
