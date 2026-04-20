"""Bluesky adapter — posts to Bluesky via the AT Protocol.

Free, no rate limit BS. Requires:
    app_settings:
        bluesky_handle    — e.g. "gladlabs.bsky.social"
        bluesky_app_password — app password from bsky.app/settings/app-passwords

Usage:
    from services.social_adapters.bluesky import post_to_bluesky
    result = await post_to_bluesky("Check out this post!", "https://gladlabs.io/posts/my-post")
"""

import re
from datetime import datetime, timezone

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

ATP_BASE = "https://bsky.social/xrpc"


async def _create_session(handle: str, password: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{ATP_BASE}/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning("[BLUESKY] Auth failed: %s %s", resp.status_code, resp.text[:200])
        return None


def _parse_facets(text: str) -> list[dict]:
    """Extract URL and mention facets for rich text."""
    facets = []
    for m in re.finditer(r"https?://\S+", text):
        facets.append({
            "index": {
                "byteStart": len(text[:m.start()].encode("utf-8")),
                "byteEnd": len(text[:m.end()].encode("utf-8")),
            },
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group()}],
        })
    return facets


async def post_to_bluesky(text: str, url: str, **kwargs) -> dict:
    """Post to Bluesky. Returns {"success": bool, "post_id": str | None, "error": str | None}."""
    handle = site_config.get("bluesky_handle", "")
    password = await site_config.get_secret("bluesky_app_password", "")

    if not handle or not password:
        return {"success": False, "post_id": None, "error": "bluesky_handle or bluesky_app_password not configured"}

    try:
        session = await _create_session(handle, password)
        if not session:
            return {"success": False, "post_id": None, "error": "Bluesky auth failed"}

        post_text = text if url in text else f"{text}\n\n{url}"
        if len(post_text) > 300:
            post_text = post_text[:297] + "..."

        record = {
            "$type": "app.bsky.feed.post",
            "text": post_text,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "facets": _parse_facets(post_text),
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{ATP_BASE}/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {session['accessJwt']}"},
                json={
                    "repo": session["did"],
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                post_id = data.get("uri", "")
                logger.info("[BLUESKY] Posted: %s", post_id)
                return {"success": True, "post_id": post_id, "error": None}
            else:
                err = resp.text[:200]
                logger.warning("[BLUESKY] Post failed: %s %s", resp.status_code, err)
                return {"success": False, "post_id": None, "error": err}

    except Exception as e:
        logger.exception("[BLUESKY] Error: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}
