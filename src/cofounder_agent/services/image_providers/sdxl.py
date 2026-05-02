"""SdxlProvider — text-to-image via the SDXL sidecar (or in-process diffusers).

Phase G follow-up (GitHub #71). Delegates to the existing
``services.image_service.ImageService.generate_image`` — the torch +
diffusers + GPU-lifecycle plumbing stays in image_service so we don't
fracture the model cache. The Provider surface adapts that to the
``ImageProvider`` Protocol, so callers can swap SDXL for Flux/DALL-E
later by setting ``plugin.image_provider.primary`` in app_settings.

Config (``plugin.image_provider.sdxl`` in app_settings):

- ``enabled`` (default true)
- ``config.negative_prompt`` (default read from
  ``app_settings.image_negative_prompt``)
- ``config.output_dir`` (default ``~/Downloads/glad-labs-generated-images``)
  — where generated PNGs land on disk. The caller is still responsible
  for uploading them somewhere durable (Cloudinary / R2).
- ``config.upload_to`` (default ``""``) — when set to ``"cloudinary"``,
  the provider uploads the generated file and returns the Cloudinary
  URL on the ImageResult. Keeps zero-network behavior as the default.

Kind: ``"generate"``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from plugins.image_provider import ImageResult

logger = logging.getLogger(__name__)


class SdxlProvider:
    """SDXL generation provider. Wraps the existing ImageService pipeline."""

    name = "sdxl"
    kind = "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        prompt = (query_or_prompt or "").strip()
        if not prompt:
            return []

        # Negative prompt: per-call override wins, then app_settings fallback.
        negative = str(config.get("negative_prompt", "") or "")
        if not negative:
            try:
                from services.site_config import site_config
                negative = site_config.get("image_negative_prompt", "") or ""
            except Exception:
                negative = ""

        # Delegate to the in-process ImageService — it owns the torch/
        # diffusers pipeline and GPU cache. We just hand it a path.
        try:
            from services.image_service import get_image_service
        except Exception as e:
            logger.warning("[SdxlProvider] image_service unavailable: %s", e)
            return []

        svc = get_image_service()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            output_path = tmp.name

        try:
            success = await svc.generate_image(
                prompt=prompt, output_path=output_path, negative_prompt=negative,
            )
        except Exception as e:
            logger.warning("[SdxlProvider] generate failed: %s", e)
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return []

        if not success or not os.path.exists(output_path):
            logger.warning("[SdxlProvider] generate returned false / no file")
            return []

        url = f"file://{output_path}"
        upload_target = str(config.get("upload_to", "") or "")

        if upload_target == "cloudinary":
            try:
                url = await _upload_to_cloudinary(output_path, prompt)
            except Exception as e:
                logger.warning(
                    "[SdxlProvider] Cloudinary upload failed (serving file:// URL): %s", e,
                )
        elif upload_target == "r2":
            try:
                url = await _upload_to_r2(output_path, prompt)
            except Exception as e:
                logger.warning(
                    "[SdxlProvider] R2 upload failed (serving file:// URL): %s", e,
                )

        return [
            ImageResult(
                url=url,
                thumbnail=url,
                photographer="Glad Labs SDXL",
                photographer_url="",
                alt_text=prompt[:200],
                source=self.name,
                search_query=prompt,
                metadata={
                    "local_path": output_path,
                    "negative_prompt": negative,
                    "upload_target": upload_target or "none",
                },
            ),
        ]


async def _upload_to_cloudinary(path: str, prompt: str) -> str:
    """Upload a generated PNG to Cloudinary and return the secure URL."""
    import asyncio

    import cloudinary
    import cloudinary.uploader

    from services.site_config import site_config

    # cloudinary_api_key + cloudinary_api_secret are is_secret=true in
    # app_settings (encrypted with enc:v1: prefix). Sync .get() returns
    # the ciphertext for is_secret rows — only get_secret() decrypts.
    # Fixes Glad-Labs/poindexter#334.
    api_key = await site_config.get_secret("cloudinary_api_key", "")
    api_secret = await site_config.get_secret("cloudinary_api_secret", "")
    cloudinary.config(
        cloud_name=site_config.get("cloudinary_cloud_name"),
        api_key=api_key,
        api_secret=api_secret,
    )

    def _upload() -> dict:
        return cloudinary.uploader.upload(
            path,
            folder="generated/",
            resource_type="image",
            tags=["sdxl", "provider"],
            context={"alt": prompt[:200]},
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _upload)
    url = result.get("secure_url", "")
    if not url:
        raise RuntimeError("Cloudinary returned empty secure_url")
    return str(url)


async def _upload_to_r2(path: str, prompt: str) -> str:
    """Upload a generated PNG to R2 via the shared r2_upload_service."""
    from services.r2_upload_service import upload_to_r2
    key = f"sdxl/{os.path.basename(path)}"
    url = await upload_to_r2(path, key, "image/png")
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    # Tag onto prompt to keep the call signature honest (unused otherwise).
    logger.debug("[SdxlProvider] uploaded %s for prompt %r", key, prompt[:40])
    return str(url)
