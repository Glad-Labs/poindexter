"""Handler: ``publishing.mastodon``.

Posts a status to a Mastodon/Fediverse instance via the existing
:func:`services.social_adapters.mastodon.post_to_mastodon` adapter.
Same shim shape as :mod:`publishing_bluesky`.

Payload shape::

    {"text": "social copy", "url": "https://gladlabs.io/posts/<slug>"}
"""

from __future__ import annotations

from typing import Any

from services.integrations.registry import register_handler
from services.social_adapters.mastodon import post_to_mastodon


@register_handler("publishing", "mastodon")
async def mastodon(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or "text" not in payload or "url" not in payload:
        raise TypeError(
            "publishing.mastodon: payload must be a dict with 'text' and 'url' keys"
        )
    return await post_to_mastodon(
        payload["text"], payload["url"], site_config=site_config,
    )
