"""Mastodon adapter — posts to any Mastodon/Fediverse instance.

Free, self-hosted or any instance. Requires:
    app_settings:
        mastodon_instance_url  — e.g. "https://mastodon.social"
        mastodon_access_token  — from Preferences > Development > New Application

Usage:
    from services.social_adapters.mastodon import post_to_mastodon
    result = await post_to_mastodon("Check out this post!", "https://gladlabs.io/posts/my-post")
"""

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


async def post_to_mastodon(text: str, url: str, **kwargs) -> dict:
    """Post a status to Mastodon. Returns {"success": bool, "post_id": str | None, "error": str | None}."""
    instance_url = site_config.get("mastodon_instance_url", "").rstrip("/")
    access_token = await site_config.get_secret("mastodon_access_token", "")

    if not instance_url or not access_token:
        return {"success": False, "post_id": None, "error": "mastodon_instance_url or mastodon_access_token not configured"}

    try:
        post_text = text if url in text else f"{text}\n\n{url}"
        if len(post_text) > 500:
            post_text = post_text[:497] + "..."

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{instance_url}/api/v1/statuses",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"status": post_text, "visibility": "public"},
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                post_id = data.get("id", "")
                post_url = data.get("url", "")
                logger.info("[MASTODON] Posted: %s", post_url)
                return {"success": True, "post_id": post_id, "error": None}
            else:
                err = resp.text[:200]
                logger.warning("[MASTODON] Post failed: %s %s", resp.status_code, err)
                return {"success": False, "post_id": None, "error": err}

    except Exception as e:
        logger.exception("[MASTODON] Error: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}
