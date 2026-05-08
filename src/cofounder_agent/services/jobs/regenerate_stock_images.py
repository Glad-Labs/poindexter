"""RegenerateStockImagesJob — replace Pexels stock photos with SDXL-generated art.

Replaces ``IdleWorker._regenerate_stock_images``. Runs every 6 hours by
default, processing up to N published posts per cycle (GPU-bound cap,
default 5).

For each post with a Pexels URL in ``featured_image_url``:

1. Ask Ollama to write a Stable Diffusion XL prompt tailored to the
   post's title (fallback to a generic photorealistic prompt if Ollama
   is unreachable).
2. Generate the image via the image service (SDXL pipeline).
3. Upload the result to Cloudinary.
4. Overwrite the post's ``featured_image_url`` with the Cloudinary URL.

When the cycle regenerates 1+ images, file a Gitea issue as an audit trail.

Config (``plugin.job.regenerate_stock_images``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 21600)
- ``config.post_limit`` (default 5) — GPU-bound cap per cycle
- ``config.prompt_model`` (default "llama3:latest") — Ollama model
  used to write SDXL prompts
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import suppress
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class RegenerateStockImagesJob:
    name = "regenerate_stock_images"
    description = "Replace Pexels featured images on published posts with SDXL-generated art"
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        cloud_url = sc.get("database_url", "") if sc is not None else ""
        if not cloud_url:
            return JobResult(ok=True, detail="no database_url — skipping", changes_made=0)

        try:
            import asyncpg
        except ImportError:
            return JobResult(ok=False, detail="asyncpg not available", changes_made=0)

        post_limit = int(config.get("post_limit", 5))
        prompt_model = str(config.get("prompt_model", "llama3:latest"))

        cloud = await asyncpg.connect(cloud_url)
        try:
            posts = await cloud.fetch(
                """
                SELECT p.id, p.title, c.name as category
                FROM posts p LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.status = 'published'
                AND p.featured_image_url LIKE '%pexels%'
                LIMIT $1
                """,
                post_limit,
            )

            if not posts:
                return JobResult(
                    ok=True, detail="no Pexels-backed posts to regenerate", changes_made=0,
                )

            # Load app_settings for negative prompt.
            negative = (
                await pool.fetchval(
                    "SELECT value FROM app_settings WHERE key = 'image_negative_prompt'",
                )
                or ""
            )

            from services.image_service import get_image_service
            svc = get_image_service()

            import cloudinary
            import cloudinary.uploader

            # cloudinary_api_key + cloudinary_api_secret are is_secret=true
            # rows — sync .get() returns ciphertext, only get_secret()
            # decrypts. Fixes Glad-Labs/poindexter#334.
            if sc is None:
                return JobResult(
                    ok=False,
                    detail="no SiteConfig in run config — cloudinary creds unreachable",
                    changes_made=0,
                )
            api_key = await sc.get_secret("cloudinary_api_key", "")
            api_secret = await sc.get_secret("cloudinary_api_secret", "")
            cloudinary.config(
                cloud_name=sc.get("cloudinary_cloud_name"),
                api_key=api_key,
                api_secret=api_secret,
            )

            regenerated = 0
            for post in posts:
                cat = (post["category"] or "technology").lower()
                prompt = await _build_sdxl_prompt(post["title"], prompt_model, sc)

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    output_path = tmp.name

                try:
                    success = await svc.generate_image(
                        prompt=prompt, output_path=output_path, negative_prompt=negative,
                    )
                    if success and os.path.exists(output_path):
                        import asyncio
                        result = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda p=output_path, c=cat: cloudinary.uploader.upload(
                                p, folder="generated/",
                                resource_type="image", tags=["featured", c],
                            ),
                        )
                        image_url = result.get("secure_url", "")
                        if image_url:
                            await cloud.execute(
                                "UPDATE posts SET featured_image_url = $1, updated_at = NOW() WHERE id = $2",
                                image_url, post["id"],
                            )
                            regenerated += 1
                            logger.info(
                                "[REGEN_IMG] Regenerated for: %s", post["title"][:40],
                            )
                        with suppress(OSError):
                            os.remove(output_path)
                except Exception as e:
                    logger.warning(
                        "[REGEN_IMG] Failed for %s: %s", post["title"][:30], e,
                    )
                    with suppress(OSError):
                        os.remove(output_path)
        finally:
            await cloud.close()

        if regenerated:
            from utils.findings import emit_finding
            emit_finding(
                source="regenerate_stock_images",
                kind="stock_image_regenerated",
                title=f"images: regenerated {regenerated} stock photos with SDXL",
                body=f"Replaced Pexels stock photos with AI-generated art for {regenerated} posts.",
                dedup_key="stock_image_regen",
                extra={"regenerated_count": regenerated},
            )

        return JobResult(
            ok=True,
            detail=f"regenerated {regenerated} image(s) (of {len(posts)} candidate)",
            changes_made=regenerated,
        )


async def _build_sdxl_prompt(title: str, model: str, site_config: Any) -> str:
    """Ask Ollama to write a tailored SDXL prompt. Fall back to a
    generic photorealistic template when Ollama is unreachable."""
    fallback = (
        f"photorealistic scene related to {title[:50]}, cinematic lighting, "
        f"4k, detailed, no people, no text"
    )
    try:
        import httpx
        ollama = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ollama}/api/generate",
                json={
                    "model": model,
                    "prompt": (
                        f"Write a Stable Diffusion XL prompt for a blog featured image about: {title[:80]}\n"
                        f"Requirements: photorealistic scene, cinematic lighting, no people, no text. "
                        f"1 sentence only. Output ONLY the prompt."
                    ),
                    "stream": False,
                    "options": {"num_predict": 100, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            generated = resp.json().get("response", "").strip().strip('"')
            if len(generated) > 20:
                return generated
    except Exception as e:
        logger.debug("[REGEN_IMG] Ollama prompt synthesis failed (using fallback): %s", e)
    return fallback
