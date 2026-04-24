"""Handler: ``outbound.vercel_isr``.

POSTs to a Next.js ISR revalidation endpoint with a shared-secret
header. Equivalent to :func:`services.revalidation_service.trigger_nextjs_revalidation`
but driven by a declarative row.

Payload shape:

.. code:: python

    {"paths": ["/", "/archive"], "tags": ["posts", "post-index"]}

``paths`` and ``tags`` are both optional; defaults come from the
row's ``config`` JSONB (``default_paths`` and ``default_tags``).

Row configuration:

- ``url`` — the site base URL (e.g. ``https://gladlabs.io``). The
  handler appends ``/api/revalidate``.
- ``signing_algorithm`` — unused for the HTTP transport (Vercel
  verifies via the ``x-revalidate-secret`` header, not a signature).
  Leave as ``none`` on the row; the secret is resolved via
  ``secret_key_ref``.
- ``secret_key_ref`` — app_settings key for the shared secret
  (typically ``revalidate_secret``, encrypted).
- ``config.default_paths`` — array of paths to revalidate when none
  is passed in the payload. Default: ``["/", "/archive"]``.
- ``config.default_tags`` — array of tags to revalidate when none is
  passed. Default: ``["posts", "post-index"]``.
- ``config.timeout_seconds`` — HTTP timeout. Default 10.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.integrations.registry import register_handler
from services.integrations.secret_resolver import resolve_secret

logger = logging.getLogger(__name__)


_DEFAULT_PATHS = ["/", "/archive"]
_DEFAULT_TAGS = ["posts", "post-index"]


@register_handler("outbound", "vercel_isr")
async def vercel_isr(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Trigger Next.js ISR revalidation by path/tag."""
    base_url = (row.get("url") or "").rstrip("/")
    if not base_url:
        raise ValueError("vercel_isr: row.url is required")

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    # Pull payload with row defaults as fallback.
    if isinstance(payload, dict):
        paths = payload.get("paths") or config.get("default_paths") or _DEFAULT_PATHS
        tags = payload.get("tags") or config.get("default_tags") or _DEFAULT_TAGS
    else:
        paths = config.get("default_paths") or _DEFAULT_PATHS
        tags = config.get("default_tags") or _DEFAULT_TAGS

    secret = await resolve_secret(row, site_config)
    if not secret:
        raise RuntimeError(
            "vercel_isr: revalidate secret not configured "
            "(set secret_key_ref or populate the referenced app_settings key)"
        )

    timeout = float(config.get("timeout_seconds") or 10.0)
    url = f"{base_url}/api/revalidate"
    headers = {
        "x-revalidate-secret": secret,
        "Content-Type": "application/json",
    }
    body = {"paths": list(paths), "tags": list(tags)}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=body)

    if response.status_code != 200:
        raise RuntimeError(
            f"vercel_isr: HTTP {response.status_code}: {response.text[:200]}"
        )

    logger.info(
        "[outbound.vercel_isr] revalidated %d path(s) + %d tag(s) on %s",
        len(body["paths"]), len(body["tags"]), base_url,
    )
    return {
        "status_code": response.status_code,
        "paths": body["paths"],
        "tags": body["tags"],
    }
