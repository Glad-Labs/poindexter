"""Cloudinary Upload Service — uploads generated media to Cloudinary.

The legacy upload destination for image/video providers, kept available
as a pluggable opt-in for operators (and future customers) who want
Cloudinary's transformations + global CDN over self-hosted R2.

**Cloudinary is not the production default.** Matt cut over to R2 in
April 2026; this module exists so the historical content backlog stays
addressable and so an operator who DOES want Cloudinary can flip
``upload_to=cloudinary`` in their image-provider config without
provider-specific code living inside each provider module.

Pre-2026-05-27 the upload helper was duplicated across
``flux_schnell.py`` and ``image_gen.py`` with subtly divergent signatures
(positional vs keyword-only ``site_config``, different tag arrays,
different error messages). Consolidated here so adding a third
caller — or a new Cloudinary feature (eager transformations, signed
URLs, etc.) — happens in one place.

Usage:
    from services.cloudinary_upload_service import upload_to_cloudinary

    url = await upload_to_cloudinary(
        "/path/to/file.png",
        prompt="a photorealistic raccoon",
        site_config=site_config,
        provider_tag="flux_schnell",
    )

Config keys (app_settings):
- ``cloudinary_cloud_name`` (non-secret) — the Cloudinary account name
- ``cloudinary_api_key`` (secret, ``is_secret=true``) — encrypted with
  ``enc:v1:`` prefix; only ``site_config.get_secret`` decrypts
- ``cloudinary_api_secret`` (secret) — same encryption story
"""

from __future__ import annotations

import asyncio
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


async def upload_to_cloudinary(
    path: str,
    prompt: str,
    *,
    site_config: Any,
    provider_tag: str,
) -> str:
    """Upload a generated media file to Cloudinary and return the secure URL.

    Args:
        path: Local filesystem path to the generated file (PNG, MP4, etc.).
        prompt: The generation prompt — surfaces in Cloudinary's ``alt`` /
            ``context`` metadata for traceability. Truncated to 200 chars
            because Cloudinary rejects unbounded context strings.
        site_config: DI-injected ``SiteConfig`` — required because the
            Cloudinary credentials are ``is_secret=true`` rows that only
            ``site_config.get_secret`` decrypts. Sync ``.get()`` returns
            ciphertext for secret rows (#334 regression).
        provider_tag: The caller's identity (``"flux_schnell"`` / ``"image_gen"``
            / ``"wan2_1"``). Lands in Cloudinary's tags so the operator
            can filter by source provider in the Cloudinary console.

    Returns:
        The Cloudinary ``secure_url`` (https) for the uploaded asset.

    Raises:
        RuntimeError: When ``site_config`` is None (missing DI seam) or
            Cloudinary returns an empty ``secure_url`` (means upload
            failed even though SDK didn't raise).
    """
    import cloudinary
    import cloudinary.uploader

    if site_config is None:
        raise RuntimeError(
            "Cloudinary upload requires site_config for the encrypted "
            "credentials — pass it via the image_provider config dict "
            "(GH#95 DI seam).",
        )

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
            tags=[provider_tag, "provider"],
            context={"alt": prompt[:200]},
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _upload)
    url = result.get("secure_url", "")
    if not url:
        raise RuntimeError("Cloudinary returned empty secure_url")
    logger.debug(
        "[cloudinary_upload_service] uploaded %s for provider=%s prompt=%r",
        path, provider_tag, prompt[:40],
    )
    return str(url)
