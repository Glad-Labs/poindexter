"""Handler: ``publishing.bluesky``.

Posts a status to Bluesky via the existing
:func:`services.social_adapters.bluesky.post_to_bluesky` adapter.
The adapter owns credentials decryption + AT Protocol session
management; this handler is a 5-line shim that adapts the registry's
``(payload, *, site_config, row, pool)`` contract to the adapter's
``(text, url, *, site_config, **kwargs)`` signature.

Payload shape::

    {"text": "social copy", "url": "https://gladlabs.io/posts/<slug>"}

Returns the adapter's existing ``{"success", "post_id", "error"}``
dict unchanged so :func:`services.social_poster._distribute_to_adapters`
can keep its result shape stable.
"""

from __future__ import annotations

from typing import Any

from services.integrations.registry import register_handler
from services.social_adapters.bluesky import post_to_bluesky


@register_handler("publishing", "bluesky")
async def bluesky(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or "text" not in payload or "url" not in payload:
        raise TypeError(
            "publishing.bluesky: payload must be a dict with 'text' and 'url' keys"
        )
    return await post_to_bluesky(
        payload["text"], payload["url"], site_config=site_config,
    )
