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
        image_service = context.get("image_service") or get_image_service()

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
            sdxl_image = await _try_sdxl_featured(
                topic=topic,
                existing_prompt=context.get("featured_image_prompt", ""),
                task_id=task_id,
                on_style_picked=lambda s: updates.update({"image_style": s}),
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
        except Exception as e:  # noqa: BLE001 — non-fatal
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
# SDXL: prompt building + rendering + R2 upload
# ---------------------------------------------------------------------------


async def _try_sdxl_featured(
    topic: str,
    existing_prompt: str,
    task_id: str | None,
    on_style_picked: Any,  # callable that records the chosen style
) -> GeneratedImage | None:
    """Full SDXL path: pick style → build prompt → render → upload to R2."""
    from services.site_config import site_config

    try:
        negative = site_config.get("image_negative_prompt", DEFAULT_NEGATIVE)
        sdxl_prompt = existing_prompt
        if not sdxl_prompt:
            sdxl_prompt = await _build_sdxl_prompt(topic, on_style_picked)

        sdxl_url = site_config.get(
            "sdxl_server_url", "http://host.docker.internal:9836",
        )
        output_path = await _render_sdxl(sdxl_url, sdxl_prompt, negative)
        if output_path is None:
            return None

        image_url = await _upload_featured_to_r2(output_path, task_id)
        source = "sdxl_cloudinary" if "cloudinary" in image_url else "sdxl_local"
        return GeneratedImage(
            url=image_url,
            photographer="AI Generated (SDXL)",
            source=source,
        )
    except Exception as e:  # noqa: BLE001 — legacy logged + fell through to pexels
        logger.info("SDXL generation skipped (%s), falling back to Pexels", e)
        return None


async def _build_sdxl_prompt(
    topic: str,
    on_style_picked: Any,
) -> str:
    """Pick a rotation style + ask Ollama for an editorial prompt."""
    from services.content_router_service import (
        _get_in_memory_recent_styles,
        _record_style_pick,
    )
    from services.site_config import site_config

    styles = _load_styles_from_settings() or list(DEFAULT_STYLES)

    recent = await _load_recent_published_styles()
    mem_recent = _get_in_memory_recent_styles()
    all_recent = set(recent) | set(mem_recent)

    available = [s for s in styles if s[0] not in all_recent] or styles
    chosen_style, style_tags = random.choice(available)  # noqa: S311 — non-crypto rotation
    _record_style_pick(chosen_style)
    on_style_picked(chosen_style)

    ollama_url = site_config.get(
        "ollama_base_url", "http://host.docker.internal:11434",
    )
    prompt_model = site_config.get(
        "inline_image_prompt_model", "llama3:latest",
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
    except Exception as e:  # noqa: BLE001
        logger.warning("[IMAGE] LLM prompt generation failed, using fallback: %s", e)
        return f"{chosen_style}, {style_tags}, no text, no faces"


def _load_styles_from_settings() -> list[tuple[str, str]]:
    """Read app_settings.image_styles (JSON array of {scene, tags})."""
    from services.site_config import site_config
    raw = site_config.get("image_styles", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    return [(s["scene"], s["tags"]) for s in parsed if "scene" in s and "tags" in s]


async def _load_recent_published_styles() -> list[str]:
    """Fetch the 5 most-recently-published posts' image_style from metadata."""
    try:
        import asyncpg
        cloud_url = os.environ.get("DATABASE_URL", "")
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
    except Exception:  # noqa: BLE001 — style dedup is best-effort
        return []


async def _render_sdxl(
    sdxl_url: str,
    sdxl_prompt: str,
    negative_prompt: str,
) -> str | None:
    """Call the SDXL server and return the local path of the generated image."""
    from services.gpu_scheduler import gpu

    async with gpu.lock("sdxl", model="sdxl_lightning"):
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

    return _resolve_sdxl_featured_response(resp)


def _resolve_sdxl_featured_response(resp: httpx.Response) -> str | None:
    """Decode the SDXL server's response to a local path."""
    from services.site_config import site_config

    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = resp.json()
        output_path = data.get("image_path", "")
        host_home = site_config.get("host_home", "")
        if host_home and output_path.startswith(host_home):
            output_path = output_path.replace(host_home, os.path.expanduser("~"), 1)
        output_path = output_path.replace("\\", "/")
        logger.info(
            "[IMAGE] Featured SDXL generated: %s (%dms)",
            os.path.basename(output_path), data.get("generation_time_ms", 0),
        )
        if output_path and os.path.exists(output_path):
            return output_path
        return None
    if ct.startswith("image/"):
        output_dir = os.path.join(
            os.path.expanduser("~"), "Downloads", "glad-labs-generated-images",
        )
        os.makedirs(output_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False, dir=output_dir,
        ) as tmp:
            tmp.write(resp.content)
            return tmp.name
    return None


async def _upload_featured_to_r2(output_path: str, task_id: str | None) -> str:
    """Upload the featured image to R2 and return the final URL."""
    try:
        from services.r2_upload_service import upload_to_r2
        r2_id = task_id or uuid.uuid4().hex[:12]
        r2_key = f"images/featured/{r2_id}.jpg"
        r2_url = await upload_to_r2(output_path, r2_key, content_type="image/jpeg")
        if r2_url:
            logger.info("Uploaded to R2: %s", r2_url[:80])
            try:
                os.remove(output_path)
            except OSError:  # best-effort
                pass
            return r2_url
        logger.warning("R2 upload returned None, using local path")
    except Exception as e:  # noqa: BLE001
        logger.warning("R2 upload failed (using local): %s", e)

    # Rewrite local-dir paths to the worker's serve URL.
    if (
        "/glad-labs-generated-images/" in output_path
        and not output_path.startswith("http")
    ):
        return f"/images/generated/{os.path.basename(output_path)}"
    return output_path
